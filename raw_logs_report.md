# Báo Cáo Phân Tích Dữ Liệu Logs (Đã Cập Nhật Sau Rerun Toàn Diện)

## Tổng quan
- **Tổng số logs (số câu truy vấn):** 7000
- **Số câu chạy đúng kết quả (Execution Match):** 4753 (67.90%)
- **Số câu đúng cấu trúc SQL (Exact Match):** 0 (0.00%)

## Phân tích Lỗi & Cảnh báo
- **Lỗi cú pháp / runtime (Syntax/Runtime Errors):** 20 (0.29%)
- **Sai kết quả ngữ nghĩa (Semantic/Wrong Result Errors):** 1845 (26.36%)
- **Lỗi Schema (Thiếu/Thừa bảng/cột):** 163 (2.33%)
- **Lỗi mạng (NETWORK_ERROR):** 218 (3.11%)

## Chi tiết các nguyên nhân gây lỗi (Root Causes)
- **CORRECT:** 4754 trường hợp
- **EXEC_WRONG_RESULT:** 1845 trường hợp
- **EXEC_SYNTAX_ERROR:** 20 trường hợp
- **SCHEMA_MISSING_TABLE:** 140 trường hợp
- **SCHEMA_HALLUCINATED_ELEMENT:** 23 trường hợp
- **NETWORK_ERROR:** 218 trường hợp
