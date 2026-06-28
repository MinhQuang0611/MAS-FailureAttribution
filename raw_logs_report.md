# Báo Cáo Phân Tích Dữ Liệu Logs (Thư mục raw_logs)

## Tổng quan
- **Tổng số logs (số câu truy vấn đã chạy):** 8940
- **Số câu chạy đúng kết quả (Execution Match):** 4747 (53.10%)
- **Số câu đúng cấu trúc SQL (Exact Match):** 424 (4.74%)
- **Thời gian thực thi trung bình (Execution Time):** 10.97 ms

## Phân phối theo độ khó

## Phân tích Lỗi & Cảnh báo
- **Số câu bị lỗi cú pháp / runtime (Syntax/Runtime Errors):** 1864 (20.85%)
- **Số câu trả về rỗng hoặc sai kết quả ngữ nghĩa (Semantic/Empty Result Errors):** 2568 (28.72%)

## Các nguyên nhân gây lỗi (Root Causes)
- **correct:** 4747 trường hợp
- **exec_wrong_result:** 2162 trường hợp
- **exec_syntax_error:** 1829 trường hợp
- **schema_missing_table:** 166 trường hợp
- **schema_hallucinated_element:** 35 trường hợp

## Mô hình (Model) đã sử dụng
- **gpt-4o:** 8939 lần chạy
