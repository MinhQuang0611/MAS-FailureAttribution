# Báo Cáo Phân Tích So Sánh Dữ Liệu Trước và Sau Rerun (Dataset train_spider.json)

## 1. Tổng quan
- **Tổng số mẫu dữ liệu trong dataset:** 7000
- **Thời gian thực hiện phân tích:** 2026-07-09

Hệ thống đã thực hiện so sánh chi tiết giữa trạng thái ban đầu (Pre-Rerun) và trạng thái sau khi đã chạy lại các mẫu lỗi cú pháp/mạng (Post-Rerun). 

---

## 2. Bảng so sánh kết quả chi tiết

| Trạng thái / Root Cause | Trước Rerun (Pre-Rerun) | Sau Rerun (Post-Rerun) | Thay đổi (Chênh lệch) | Tỷ lệ trước (%) | Tỷ lệ sau (%) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **CORRECT (Thành công)** | 4066 | 4754 | **+688** | 58.09% | 67.91% |
| **EXEC_WRONG_RESULT (Sai ngữ nghĩa)** | 1669 | 1845 | 176 | 23.84% | 26.36% |
| **EXEC_SYNTAX_ERROR (Lỗi cú pháp SQL)** | 637 | 20 | -617 | 9.10% | 0.29% |
| **SCHEMA_MISSING_TABLE (Lỗi Schema thiếu)** | 136 | 140 | 4 | 1.94% | 2.00% |
| **SCHEMA_HALLUCINATED_ELEMENT (Lỗi Schema thừa)** | 28 | 23 | -5 | 0.40% | 0.33% |
| **NETWORK_ERROR (Lỗi kết nối mạng)** | 464 | 218 | **-246** | 6.63% | 3.11% |

---

## 3. Nhận xét & Đánh giá hiệu quả Rerun

1. **Độ chính xác tăng trưởng (Execution Match):**
   - Tỷ lệ câu truy vấn chạy đúng kết quả tăng từ **58.09%** (4066 câu) lên **67.91%** (4754 câu).
   - Hệ thống đã sửa thành công thêm **688** trường hợp lỗi nhờ cơ chế rerun và tự động sửa lỗi (Self-Correction).

2. **Lỗi mạng (NETWORK_ERROR):**
   - Ban đầu có **464** trường hợp bị lỗi kết nối mạng (chiếm 6.63%).
   - Sau đợt chạy lại `network_rerun`, vẫn còn lại **218** trường hợp bị lỗi mạng (chiếm 3.11%). Điều này cho thấy hệ thống mạng đã ổn định hơn nhưng vẫn còn một số lượng nhỏ các truy vấn bị ngắt kết nối giữa chừng hoặc gặp lỗi pool kết nối.

3. **Lỗi cú pháp cứng (EXEC_SYNTAX_ERROR):**
   - Giảm mạnh từ **637** câu xuống còn **20** câu. 
   - Phần lớn các lỗi cú pháp đã được sửa đúng hoặc chuyển đổi thành lỗi ngữ nghĩa (EXEC_WRONG_RESULT) sau khi được thực thi đầy đủ.
