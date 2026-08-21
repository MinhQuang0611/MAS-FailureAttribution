# Báo Cáo Phân Tích Dữ Liệu Logs Sau Rerun (Thư mục rerun_failed)

## Tổng quan
- **Tổng số logs (số câu đã chạy lại):** 2934
- **Số câu ĐÃ ĐƯỢC SỬA ĐÚNG (Tự động Self-Correction thành công):** 671 (22.87%)
- **Số câu vẫn sai:** 2263 (77.13%)

## Các nguyên nhân gây lỗi còn lại (Root Causes)
- **CORRECT (Đã sửa đúng):** 671 trường hợp
- **EXEC_WRONG_RESULT:** 1618 trường hợp
- **EXEC_SYNTAX_ERROR:** 493 trường hợp
- **SCHEMA_MISSING_TABLE:** 130 trường hợp
- **SCHEMA_HALLUCINATED_ELEMENT:** 22 trường hợp

## Đánh giá hiệu quả của hệ thống Agentic:
- Nhờ cơ chế Self-Correction, hệ thống đã cứu được **671** lỗi cú pháp/logic từ đợt chạy trước.
- Số lượng lỗi cú pháp cứng (EXEC_SYNTAX_ERROR) hiện còn: **493** ca.
