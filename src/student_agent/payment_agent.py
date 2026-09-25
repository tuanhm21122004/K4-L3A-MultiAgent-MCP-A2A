from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def investigate_payment(
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    fetch_payments: bool = True,
    fetch_refund: bool = False,
) -> dict[str, Any]:
    """Payment specialist: audits payments, split payments, duplicate charges or refund timeline."""
    evidence_refs: list[str] = []
    payments_data: list[dict[str, Any]] = []
    payment_references: list[str] = ["1"]
    total_paid = 0.0
    is_duplicate = False
    duplicate_amount = 0.0
    ev_pay: str | None = None

    # 1. Fetch payments when investigating payment-related issues
    if fetch_payments:
        pay_res = await gateway.call("get_order_payments", case_id=case_id, order_id=order_id)
        ev_pay = pay_res["evidence_ref"]
        evidence_refs.append(ev_pay)
        trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="payment-agent",
            tool_name="get_order_payments",
            evidence_refs=[ev_pay],
        )
        payments_data = pay_res.get("data", [])
        refs: list[str] = []
        seen_payment_specs: list[tuple[str, float]] = []

        for p in payments_data:
            p_seq = str(p.get("payment_sequential", "1"))
            if p_seq not in refs:
                refs.append(p_seq)
            val = 0.0
            try:
                val = float(p.get("payment_value", 0.0))
            except (ValueError, TypeError):
                pass
            total_paid += val

            p_type = str(p.get("payment_type"))
            spec = (p_type, val)
            if spec in seen_payment_specs:
                is_duplicate = True
                duplicate_amount += val
            else:
                seen_payment_specs.append(spec)

        if refs:
            payment_references = refs

    # 2. Fetch refund timeline when investigating refund-related issues
    refund_events: list[dict[str, Any]] = []
    refund_ref: str | None = None
    if fetch_refund:
        try:
            ref_res = await gateway.call("get_refund_timeline", case_id=case_id, order_id=order_id)
            refund_ref = ref_res.get("evidence_ref")
            if refund_ref:
                evidence_refs.append(refund_ref)
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="payment-agent",
                    tool_name="get_refund_timeline",
                    evidence_refs=[refund_ref],
                )
            refund_events = ref_res.get("data", {}).get("events", [])
        except Exception:
            pass

    return {
        "order_id": order_id,
        "payments": payments_data,
        "payment_references": payment_references,
        "total_paid": round(total_paid, 2),
        "is_duplicate": is_duplicate,
        "duplicate_amount": round(duplicate_amount, 2),
        "refund_events": refund_events,
        "refund_ref": refund_ref,
        "evidence_refs": evidence_refs,
        "payment_ref": ev_pay or refund_ref,
    }
