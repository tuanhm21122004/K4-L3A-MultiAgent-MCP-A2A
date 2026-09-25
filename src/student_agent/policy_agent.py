from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def evaluate_policy(
    case_id: str,
    policy_version: str,
    claims: list[dict[str, Any]],
    order_info: dict[str, Any],
    shipment_info: dict[str, Any],
    payment_info: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Policy specialist: evaluates authoritative rules, determines primary issue and financial resolution."""
    # 1. Fetch policy
    pol_res = await gateway.call("get_policy", case_id=case_id, policy_version=policy_version)
    ev_policy = pol_res["evidence_ref"]
    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="policy-agent",
        tool_name="get_policy",
        evidence_refs=[ev_policy],
    )
    policy_data = pol_res.get("data", {})
    rules = policy_data.get("rules", {})

    # Extract primary claim topic
    claim_topics = [c.get("topic") for c in claims if c.get("topic") != "requested_full_refund"]
    primary_claimed_topic = claim_topics[0] if claim_topics else "unsupported_claim"

    # Evaluate facts deterministically
    order_status = order_info.get("order_status")
    refund_events = payment_info.get("refund_events", [])
    is_duplicate = payment_info.get("is_duplicate", False)
    is_seller_late = shipment_info.get("is_seller_late", False)
    is_logistics_late = shipment_info.get("is_logistics_late", False)

    # Deterministic rule resolution based on authoritative evidence and claim hypothesis
    detected_issue: str
    if primary_claimed_topic == "canceled_order_paid":
        detected_issue = "canceled_order_paid" if order_status == "canceled" else "unsupported_claim"
    elif primary_claimed_topic == "unavailable_order_paid":
        detected_issue = "unavailable_order_paid" if order_status == "unavailable" else "unsupported_claim"
    elif primary_claimed_topic == "late_delivery_seller":
        detected_issue = "late_delivery_seller" if is_seller_late else "unsupported_claim"
    elif primary_claimed_topic == "late_delivery_logistics":
        detected_issue = "late_delivery_logistics" if is_logistics_late else "unsupported_claim"
    elif primary_claimed_topic == "duplicate_charge":
        detected_issue = "duplicate_charge" if is_duplicate else "unsupported_claim"
    elif primary_claimed_topic == "refund_failed":
        has_failed = any(e.get("status") == "failed" for e in refund_events)
        detected_issue = "refund_failed" if (has_failed or not refund_events) else "unsupported_claim"
    elif primary_claimed_topic == "refund_pending":
        has_pending = any(e.get("status") == "pending" for e in refund_events)
        detected_issue = "refund_pending" if (has_pending or not refund_events) else "unsupported_claim"
    elif primary_claimed_topic == "valid_split_payment":
        detected_issue = "valid_split_payment"
    elif primary_claimed_topic == "payment_mismatch":
        detected_issue = "payment_mismatch"
    elif primary_claimed_topic == "unsupported_claim":
        detected_issue = "unsupported_claim"
    else:
        detected_issue = "unsupported_claim"

    primary_issue = detected_issue
    rule = rules.get(primary_issue, {})

    case_status = rule.get("case_status", "no_action" if primary_issue in ["unsupported_claim", "valid_split_payment"] else "action_required")
    recommended_action = rule.get("recommended_action", "document_no_action")
    refund_brl = float(rule.get("refund_brl", 0.0))

    # Determine responsible party
    seller_ids = order_info.get("seller_ids", [])
    primary_seller_id = seller_ids[0] if seller_ids else f"seller-{order_info.get('order_id', '')[:12]}"

    responsible_parties: list[dict[str, Any]] = []
    rule_parties = rule.get("responsible_parties", [])
    if rule_parties:
        for rp in rule_parties:
            p_type = rp.get("party_type", "platform")
            if p_type == "seller":
                responsible_parties.append({"party_type": "seller", "party_id": primary_seller_id})
            else:
                responsible_parties.append({"party_type": p_type, "party_id": None})
    else:
        if primary_issue in ["late_delivery_seller", "unavailable_order_paid"]:
            responsible_parties.append({"party_type": "seller", "party_id": primary_seller_id})
        elif primary_issue == "late_delivery_logistics":
            responsible_parties.append({"party_type": "logistics_provider", "party_id": None})
        elif primary_issue in ["duplicate_charge", "payment_mismatch", "refund_failed", "refund_pending"]:
            responsible_parties.append({"party_type": "payment_provider", "party_id": None})
        elif primary_issue in ["unsupported_claim", "valid_split_payment"]:
            responsible_parties.append({"party_type": "customer", "party_id": None})
        else:
            responsible_parties.append({"party_type": "platform", "party_id": None})

    decision_code = f"RULE_{primary_issue.upper()}"
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy-agent",
        decision_code=decision_code,
    )

    return {
        "primary_issue": primary_issue,
        "case_status": case_status,
        "recommended_action": recommended_action,
        "refund_brl": refund_brl,
        "responsible_parties": responsible_parties,
        "policy_ref": ev_policy,
    }
