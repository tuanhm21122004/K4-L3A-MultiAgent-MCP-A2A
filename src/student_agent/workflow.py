from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .order_agent import investigate_order
from .payment_agent import investigate_payment
from .policy_agent import evaluate_policy
from .shipment_agent import investigate_shipment
from .trace import TraceWriter
from .verifier import verify_and_build_output


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent investigation workflow for a single case.

    Lifecycle sequence:
    1. coordinator assigns task to order-agent (task_assigned)
    2. order-agent investigates authoritative order & items (tool_result_consumed)
    3. handoff to appropriate domain specialist (shipment-agent or payment-agent)
    4. specialist gathers authoritative domain evidence (tool_result_consumed)
    5. handoff to policy-agent
    6. policy-agent queries policy rules and evaluates decision (policy_decided)
    7. handoff to verifier
    8. verifier checks all invariants and outputs result (verification_completed)
    """
    case_id = case["case_id"]
    order_id = case.get("customer_request", {}).get("claimed_order_id") or case.get("order_id", "")
    policy_version = case.get("policy_version", "EC_POLICY_V1")
    claims = case.get("customer_request", {}).get("claims", [])

    claim_topics = [c.get("topic") for c in claims if c.get("topic") != "requested_full_refund"]
    primary_claimed_topic = claim_topics[0] if claim_topics else "unsupported_claim"

    # 1. Coordinator assigns task to order-agent
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="order-agent",
    )

    shipment_info: dict[str, Any] = {"evidence_refs": []}
    payment_info: dict[str, Any] = {"evidence_refs": [], "payment_references": ["1"]}

    # Route based on the claim hypothesis to optimize tool budget and evidence precision
    if primary_claimed_topic in ["late_delivery_seller", "late_delivery_logistics", "unsupported_claim"]:
        # Order specialist investigates order
        order_info = await investigate_order(case_id, order_id, gateway, trace, fetch_items=False)

        # Handoff to shipment specialist
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="order-agent",
            target="shipment-agent",
        )
        shipment_info = await investigate_shipment(case_id, order_id, gateway, trace)

        # Handoff from shipment-agent to policy-agent
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="shipment-agent",
            target="policy-agent",
        )

    elif primary_claimed_topic in ["refund_pending", "refund_failed"]:
        # Order specialist investigates order
        order_info = await investigate_order(case_id, order_id, gateway, trace, fetch_items=False)

        # Handoff to payment specialist for refund ledger
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="order-agent",
            target="payment-agent",
        )
        payment_info = await investigate_payment(
            case_id, order_id, gateway, trace, fetch_payments=False, fetch_refund=True
        )

        # Handoff from payment-agent to policy-agent
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="payment-agent",
            target="policy-agent",
        )

    elif primary_claimed_topic in ["duplicate_charge", "payment_mismatch", "valid_split_payment"]:
        # Order specialist investigates order
        order_info = await investigate_order(case_id, order_id, gateway, trace, fetch_items=False)

        # Handoff to payment specialist
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="order-agent",
            target="payment-agent",
        )
        payment_info = await investigate_payment(
            case_id, order_id, gateway, trace, fetch_payments=True, fetch_refund=False
        )

        # Handoff from payment-agent to policy-agent
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="payment-agent",
            target="policy-agent",
        )

    else:
        # Canceled / Unavailable orders
        order_info = await investigate_order(case_id, order_id, gateway, trace, fetch_items=False)

        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="order-agent",
            target="payment-agent",
        )
        payment_info = await investigate_payment(
            case_id, order_id, gateway, trace, fetch_payments=True, fetch_refund=False
        )

        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="payment-agent",
            target="policy-agent",
        )

    # 6. Policy agent evaluates policy rules
    policy_decision = await evaluate_policy(
        case_id,
        policy_version,
        claims,
        order_info,
        shipment_info,
        payment_info,
        gateway,
        trace,
    )

    # 7. Handoff to verifier
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy-agent",
        target="verifier",
    )

    # 8. Verifier validates invariants and packages output
    output = verify_and_build_output(
        case_id,
        order_id,
        claims,
        order_info,
        shipment_info,
        payment_info,
        policy_decision,
        trace,
    )

    return output
