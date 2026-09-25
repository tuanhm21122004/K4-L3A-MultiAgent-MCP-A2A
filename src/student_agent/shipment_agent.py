from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def investigate_shipment(
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Shipment specialist: gathers delivery timeline, limits and carrier events."""
    ship_res = await gateway.call("get_shipment_summary", case_id=case_id, order_id=order_id)
    ev_shipment = ship_res["evidence_ref"]
    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="shipment-agent",
        tool_name="get_shipment_summary",
        evidence_refs=[ev_shipment],
    )
    ship_data = ship_res.get("data", {})

    delivered_carrier_at = ship_data.get("delivered_carrier_at")
    delivered_customer_at = ship_data.get("delivered_customer_at")
    estimated_delivery_at = ship_data.get("estimated_delivery_at")
    shipping_limits = ship_data.get("shipping_limits", [])
    events = ship_data.get("events", [])

    # Check for delivery late events directly
    late_actor: str | None = None
    for event in events:
        if event.get("event_type") == "delivered_late" and event.get("status") == "confirmed":
            late_actor = event.get("actor")

    # Also compare timestamps
    is_seller_late = False
    is_logistics_late = False

    if late_actor == "seller":
        is_seller_late = True
    elif late_actor == "logistics_provider":
        is_logistics_late = True
    else:
        # Compare carrier handoff vs limit
        if delivered_carrier_at and shipping_limits:
            for limit in shipping_limits:
                limit_at = limit.get("shipping_limit_at")
                if limit_at and delivered_carrier_at > limit_at:
                    is_seller_late = True
                    break
        # Compare customer delivery vs estimated
        if delivered_customer_at and estimated_delivery_at:
            if delivered_customer_at > estimated_delivery_at:
                is_logistics_late = True

    return {
        "order_id": order_id,
        "delivered_carrier_at": delivered_carrier_at,
        "delivered_customer_at": delivered_customer_at,
        "estimated_delivery_at": estimated_delivery_at,
        "shipping_limits": shipping_limits,
        "events": events,
        "is_seller_late": is_seller_late,
        "is_logistics_late": is_logistics_late,
        "late_actor": late_actor,
        "evidence_refs": [ev_shipment],
        "shipment_ref": ev_shipment,
    }
