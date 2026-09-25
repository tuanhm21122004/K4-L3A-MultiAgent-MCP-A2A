# L3A Architecture Record

## 1. System overview

Luồng điều tra khiếu nại thương mại điện tử kết nối các Specialist Agents qua A2A protocol, truy xuất dữ liệu có thẩm quyền từ MCP Evidence Gateway và kiểm định tính toàn vẹn qua Verifier Agent:

```text
               ┌────────────────┐
               │  Coordinator   │ (Task Assignment & Lifecycle)
               └───────┬────────┘
        task_assigned  │
                       ▼
            ┌──────────────────────┐
            │   Order Specialist   │ <─── [get_order, get_order_items, get_sellers]
            └──────────┬───────────┘
              handoff  │
                       ▼
            ┌──────────────────────┐
            │ Shipment Specialist  │ <─── [get_shipment_summary]
            └──────────┬───────────┘
              handoff  │
                       ▼
            ┌──────────────────────┐
            │  Payment Specialist  │ <─── [get_order_payments, get_refund_timeline]
            └──────────┬───────────┘
              handoff  │
                       ▼
            ┌──────────────────────┐
            │  Policy Specialist   │ <─── [get_policy] (Deterministic Rule Evaluation)
            └──────────┬───────────┘
              handoff  │
                       ▼
            ┌──────────────────────┐
            │    Verifier Agent    │ (Invariants, Schema, Cross-field Consistency)
            └──────────┬───────────┘
  verification_ok      │
                       ▼
               ┌────────────────┐
               │ Output & Trace │
               └────────────────┘
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff | Quyền gọi Tool |
| --- | --- | --- | --- | --- |
| Coordinator | `inputs/<case_id>.json` | Quản lý vòng đời case, phân phối tác vụ ban đầu, kết xuất output cuối cùng. | Giao việc cho `order-agent` | Không gọi tool MCP |
| Order/item | `case_id`, `order_id` | Truy vấn thông tin đơn hàng, danh mục sản phẩm, tính tổng tiền và các seller liên quan. | `order_info` ➔ `shipment-agent` | `get_order`, `get_order_items`, `get_sellers` |
| Shipment | `case_id`, `order_id` | Đánh giá mốc thời gian giao nhận, hạn giao hàng của seller và sự kiện trễ hạn của đơn vị vận chuyển. | `shipment_info` ➔ `payment-agent` | `get_shipment_summary` |
| Payment | `case_id`, `order_id` | Đối soát các dòng thanh toán, phát hiện duplicate charge, kiểm tra tiến trình hoàn tiền. | `payment_info` ➔ `policy-agent` | `get_order_payments`, `get_refund_timeline` |
| Policy | `claims`, `order_info`, `shipment_info`, `payment_info` | Đối chiếu quy tắc chính sách, xác định `primary_issue`, tính toán số tiền hoàn và bên chịu trách nhiệm. | `policy_decision` ➔ `verifier` | `get_policy` |
| Verifier | Toàn bộ dữ liệu tổng hợp & quyết định | Kiểm tra tính bất biến (invariants), quan hệ thực thể, ràng buộc số tiền và độ chuẩn xác của schema. | Output JSON chuẩn hóa | Không gọi tool MCP |

## 3. A2A protocol

- **Message Envelope & Correlation:** Tất cả các sự kiện và thông điệp chuyển giao giữa các agent đều được gắn kèm mã định danh `case_id` nhằm đảm bảo correlation độc lập tuyệt đối giữa các phiên điều tra.
- **Handoff Chain:** Chuỗi điều tra tuân thủ cấu trúc pipeline đơn hướng tuần tự:
  `coordinator` ➔ `order-agent` ➔ `shipment-agent` ➔ `payment-agent` ➔ `policy-agent` ➔ `verifier` ➔ `coordinator`.
- **Trace Event Ordering:** Thứ tự phát sinh event trong `traces/trace.jsonl` được kiểm soát chặt chẽ:
  `case_received` ➔ `task_assigned` ➔ `tool_result_consumed` (từng agent) ➔ `handoff` ➔ `policy_decided` ➔ `verification_completed` ➔ `case_finalized`.
- **Loop Prevention:** Pipeline một chiều ngăn chặn hoàn toàn khả năng lặp vô hạn.

## 4. Evidence lifecycle

- **Validation:** Mọi phản hồi từ MCP Gateway được thẩm tra tức thì theo schema `mcp-evidence-response-v1.schema.json` trước khi đưa vào luồng nghiệp vụ.
- **Provenance & Scoping:** Mỗi `evidence_ref` sinh ra (`ev_...`) chỉ được gắn vào case tương ứng; tuyệt đối không dùng bằng chứng chéo giữa các case.
- **Audited Emission:** Ngay khi dữ liệu bằng chứng được một specialist tiếp nhận, hệ thống ghi nhận sự kiện `tool_result_consumed` với `actor`, `tool_name` và danh sách `evidence_refs`.
- **Output Linkage:** Tất cả các evidence ref được đưa vào trường `evidence_refs` của output chính và liên kết chính xác với từng `claim_assessment`.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout / ReadError | Có (tối đa 3 lần với backoff 0.5s - 1.5s, tự động reconnect session) | Tự động tạo kết nối mới và thử lại | Không phát sinh event lỗi nếu retry thành công |
| Tool data not found (404/no record) | Không | Coi trường dữ liệu đó là rỗng (ví dụ: đơn hàng không có lịch sử refund) | Bỏ qua tool consumption cho trường hợp rỗng |
| Source conflict (Khách claim vs Hồ sơ thẩm quyền) | Không | Dữ liệu MCP Gateway luôn là ground truth, ghi nhận vào `data_conflicts` | `resolution_code: "AUTHORITATIVE_RECORD"` |
| Invalid specialist result | Có (Verifier reject) | Điều chỉnh về trạng thái an toàn `unsupported_claim` với `recommended_refund_brl: 0.0` | `decision_code: "VERIFICATION_FALLBACK"` |

## 6. Verification invariants

Trước khi xuất file output cuối cùng, Verifier Agent thẩm định bắt buộc các quy tắc sau:
1. **Schema Compliance:** Khớp 100% với `l3a-output-v2.schema.json`.
2. **Refund Invariant:** Nếu `case_status == "no_action"` hoặc `"needs_investigation"` thì `recommended_refund_brl = 0.0` và `refund_lines = []`.
3. **Refund Line Matching:** Tổng giá trị các dòng trong `refund_lines` phải khớp chính xác với `recommended_refund_brl`.
4. **Seller Responsibility:** Khi `party_type == "seller"`, mã `party_id` bắt buộc phải là một mã định danh seller hợp lệ và nằm trong danh sách `affected_entities.seller_ids`.
5. **Action Uniqueness:** Danh sách `resolution_actions` không chứa phần tử trùng lặp và có tối thiểu 1 hành động cụ thể.
6. **Evidence Authenticity:** Toàn bộ các mã `evidence_ref` trong output và claim assessments phải xuất phát từ các lệnh gọi MCP hợp lệ của chính case đó.
7. **Calibrated Confidence:** Mức độ tin cậy được hiệu chuẩn chặt chẽ ở mức 0.98 cho các kết luận có bằng chứng thẩm quyền đầy đủ.

## 7. Reproducibility

- **Ngôn ngữ:** Python 3.11+
- **Môi trường & Thư viện:** `httpx2`, `mcp`, `jsonschema`, `referencing`, `pytest`.
- **Cơ chế ra quyết định:** Hoàn toàn dựa trên tập luật tất định (deterministic rule-based evaluation) đối chiếu trực tiếp với authoritative e-commerce database, đảm bảo tính nhất quán 100% qua mọi lần chạy độc lập.
- **Lệnh chạy thực thi:**
  ```bash
  day09 validate-inputs
  day09 run
  day09 validate
  day09 package --output dist/submission.zip
  ```
