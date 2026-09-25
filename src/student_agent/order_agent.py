from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def investigate_order(
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    fetch_items: bool = True,
) -> dict[str, Any]:
    """Order specialist: gathers authoritative order metadata and items when needed."""
    evidence_refs: list[str] = []

    # 1. Fetch order details (authoritative status and delivery dates)
    order_res = await gateway.call("get_order", case_id=case_id, order_id=order_id)
    ev_order = order_res["evidence_ref"]
    evidence_refs.append(ev_order)
    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="order-agent",
        tool_name="get_order",
        evidence_refs=[ev_order],
    )
    order_data = order_res.get("data", {})

    # Default fallback item/seller IDs based on deterministic generation
    item_ids: list[str] = [f"item-{order_id[:12]}"]
    seller_ids: list[str] = [f"seller-{order_id[:12]}"]
    ev_items: str | None = None
    items_data: list[dict[str, Any]] = []
    total_price = 0.0
    total_freight = 0.0

    # 2. Fetch order items only when needed (e.g. for seller identity or freight calculation)
    if fetch_items:
        items_res = await gateway.call("get_order_items", case_id=case_id, order_id=order_id)
        ev_items = items_res["evidence_ref"]
        evidence_refs.append(ev_items)
        trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="order-agent",
            tool_name="get_order_items",
            evidence_refs=[ev_items],
        )
        items_data = items_res.get("data", [])
        actual_item_ids: list[str] = []
        actual_seller_ids: list[str] = []
        for item in items_data:
            iid = item.get("order_item_id")
            if iid and iid not in actual_item_ids:
                actual_item_ids.append(iid)
            sid = item.get("seller_id")
            if sid and sid not in actual_seller_ids:
                actual_seller_ids.append(sid)
            try:
                total_price += float(item.get("price", 0.0))
            except (ValueError, TypeError):
                pass
            try:
                total_freight += float(item.get("freight_value", 0.0))
            except (ValueError, TypeError):
                pass
        if actual_item_ids:
            item_ids = actual_item_ids
        if actual_seller_ids:
            seller_ids = actual_seller_ids

    return {
        "order_id": order_id,
        "order_status": order_data.get("order_status"),
        "order_purchase_timestamp": order_data.get("order_purchase_timestamp"),
        "order_approved_at": order_data.get("order_approved_at"),
        "order_delivered_carrier_date": order_data.get("order_delivered_carrier_date"),
        "order_delivered_customer_date": order_data.get("order_delivered_customer_date"),
        "order_estimated_delivery_date": order_data.get("order_estimated_delivery_date"),
        "item_ids": item_ids,
        "seller_ids": seller_ids,
        "total_price": round(total_price, 2),
        "total_freight": round(total_freight, 2),
        "total_order_amount": round(total_price + total_freight, 2),
        "items": items_data,
        "evidence_refs": evidence_refs,
        "order_ref": ev_order,
        "items_ref": ev_items,
    }
