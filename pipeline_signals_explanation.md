# Chi Tiết Các Thông Tin Logs (Tín Hiệu x1 → x13) Trong Hệ Thống T2SQL

Hệ thống lưu lại log của mỗi lần chạy dưới dạng một `PipelineLogEntry` theo một cấu trúc dữ liệu JSON rất chặt chẽ. Toàn bộ tiến trình (pipeline) được chia làm 4 bước (từ A đến D), tương ứng với 13 tín hiệu (từ `x1` đến `x13`) kèm theo các bước "Detection" (So sánh & Đánh giá). 

Dưới đây là giải thích chi tiết, đặc biệt tập trung vào `x1`, `x2`, mục đích của chúng và cách hệ thống thực hiện so sánh đối chiếu.

---

## BƯỚC A: INTENT UNDERSTANDING (Hiểu Ý Định Người Dùng) - [x1, x2, x3]

Đây là bước hệ thống thu thập và phân tích yêu cầu của người dùng trước khi đụng vào Database hay sinh SQL.

### 1. `x1` - Raw User Query (Câu hỏi gốc)
- **Nó là gì?**: Là dữ liệu đầu vào nguyên thủy nhất từ người dùng.
- **Chứa thông tin gì?**: 
  - `question`: Câu hỏi bằng ngôn ngữ tự nhiên gốc (ví dụ: "Cho tôi biết số lượng sinh viên?").
  - `db_id`: Mã của database cần truy vấn.
  - `difficulty`, `source`: Độ khó và nguồn của câu hỏi (dùng để phân tích đánh giá).
- **Tác dụng**: Là "sự thật gốc" (ground truth) của ý định người dùng, chưa hề bị AI hay bất cứ logic nào bóp méo. 

### 2. `x2` - External Knowledge (Kiến thức / Ràng buộc bên ngoài)
- **Nó là gì?**: Các thông tin, bối cảnh, hoặc quy tắc bổ sung được tiêm vào (inject) hệ thống mà không nằm trong câu hỏi gốc `x1`.
- **Chứa thông tin gì?**:
  - `constraints`: Các ràng buộc bắt buộc (ví dụ: "Phải dùng bảng A", "Định dạng lỗi cần chú ý là missing_constraint").
  - `domain_hints`: Các gợi ý nghiệp vụ cụ thể của domain đó.
  - `raw_context`: Bối cảnh thô khác.
- **Tác dụng**: Bổ khuyết cho câu hỏi gốc `x1`. Thực tế người dùng thường hỏi rất vắn tắt, `x2` giúp hệ thống biết thêm "luật chơi" hoặc định nghĩa nghiệp vụ trước khi xử lý.

### 3. `x3` - Clarified Intent (Ý định đã được làm rõ)
- **Nó là gì?**: Ý định cuối cùng mà hệ thống/LLM chốt lại sau khi đã gom `x1` và `x2` lại với nhau.
- **Chứa thông tin gì?**:
  - `clarified_question`: Câu hỏi đã được viết lại cho rõ ràng, tường minh.
  - `subtasks`: Các bước/tác vụ con cần làm (ví dụ: 1. Tìm sinh viên, 2. Đếm số lượng).
  - `detected_conditions`: Các điều kiện lọc (WHERE) được nhận diện (ví dụ: Tuổi > 18).
- **Tác dụng**: Là "kim chỉ nam" chính thức. Các bước sinh SQL sau này sẽ bám sát vào `x3` thay vì bám vào `x1` thô.

### 🔴 Cách So Sánh `x1` + `x2` ↔ `x3` (Step A Detection)
Hệ thống sử dụng các phép đo đạc tự động (heuristic/rules) để **đảm bảo rằng `x3` (ý định làm rõ) KHÔNG bị sai lệch so với `x1` và `x2`**. Các bài test cụ thể bao gồm:
1. **Keyword Coverage Score (Độ phủ từ khóa)**: Tính tỷ lệ các từ khóa trong `x1` xuất hiện lại trong `x3`.
2. **Semantic Consistency (Nhất quán ngữ nghĩa)**: Hệ thống coi là "Pass" nếu độ phủ từ khóa đạt trên 85% hoặc câu hỏi làm rõ không bị biến dạng so với bản gốc.
3. **Over-inference / Hallucinated assumptions (Suy diễn quá mức/Ảo giác)**: Hệ thống "bắt lỗi" nếu `x3` tự nhiên đẻ ra thêm >= 3 từ khóa lạ hoắc mà `x1` không hề nhắc tới. Đây là dấu hiệu LLM tự "bịa" thêm điều kiện.
4. **Missing constraints (Thiếu ràng buộc)**: Hệ thống kiểm tra xem `x3` có bị quá ngắn gọn so với `x1` không (chiều dài < 50%). Nếu có, chứng tỏ hệ thống đã hiểu sót ý người dùng.
5. **Misinterpreted intent (Hiểu sai ý)**: Đánh giá xem hệ thống có bóp méo mục đích gốc không (thường được gán nhãn).

---

## BƯỚC B: SCHEMA LINKING (Liên Kết Dữ Liệu) - [x4, x5, x6]

Bước này xác định những bảng/cột nào trong Database sẽ được dùng.

- **`x4` (Full Schema)**: Là toàn bộ cấu trúc của Database (tất cả các Tables và Columns).
- **`x5` (Filtered Schema)**: Là cấu trúc đã được rút gọn/cắt tỉa (Pruning). Hệ thống lọc bỏ những bảng không liên quan để tránh làm LLM bị nhiễu.
- **`x6` (Schema Alignment)**: Bảng ánh xạ cụ thể. Nó map từng từ (token) trong câu hỏi `x3` sang chính xác một Bảng hoặc Cột cụ thể, kèm theo điểm tự tin (`confidence`).

### 🔴 Cách So Sánh (Step B Detection)
- **Missing tables/columns**: So chiếu `x5` xem hệ thống có lỡ tay "cắt tỉa" nhầm các bảng/cột thực sự cần thiết không.
- **Hallucinated schema elements**: Phát hiện xem `x6` có tự "bịa" ra việc map một từ vào một cột/bảng vốn KHÔNG TỒN TẠI trong `x4` hay không.
- **Ambiguous token alignment**: Phát hiện khi điểm tự tin `confidence` nằm lơ lửng ở khoảng 0.3 đến 0.55 (không chắc chắn).

---

## BƯỚC C: SQL GENERATION (Sinh SQL) - [x7, x8, x9]

Đây là trái tim của hệ thống: sinh ra câu truy vấn.

- **`x7` (Query Plan)**: Kế hoạch truy vấn. Liệt kê các bước luận lý (vd: dùng JOIN, dùng GROUP BY) và luồng suy luận của mô hình (`reasoning_trace`).
- **`x8` (SQL Skeleton)**: Khung sườn SQL (vd: `SELECT _ FROM _ WHERE _ GROUP BY _`). Khung này giữ lại các mệnh đề bắt buộc phải có (`expected_clauses`).
- **`x9` (Candidate SQLs)**: Các ứng viên SQL hoàn chỉnh được sinh ra. Có thể sinh ra nhiều ứng viên và chọn lấy ứng viên tốt nhất.

### 🔴 Cách So Sánh (Step C Detection)
- **Plan Logical Valid**: So sánh ngược `x7` và `x9`. Nếu kế hoạch `x7` bảo là cần `JOIN`, nhưng câu SQL chốt trong `x9` lại không hề có chữ `JOIN` -> Lỗi bất đồng bộ kế hoạch.
- **Skeleton Missing Clauses**: Đem khung sườn `x8` đối chiếu với SQL thực tế `x9`. Đảm bảo không bị rớt các mệnh đề quan trọng như `WHERE`, `ORDER BY`.
- **Self-consistency Score**: Đánh giá sự đồng thuận giữa nhiều ứng viên SQL được sinh ra.

---

## BƯỚC D: EXECUTION & VERIFICATION (Thực Thi & Phân Tích Lỗi) - [x10, x11, x12, x13]

Mang SQL đi chạy thật trong Database.

- **`x10` (Runtime Error)**: Bắt các lỗi văng ra từ Database như SyntaxError (sai cú pháp), lỗi cột không tồn tại, v.v.
- **`x11` (Execution Result)**: Kết quả thực thi thực tế (danh sách các dòng dữ liệu, số lượng dòng, thời gian chạy). Đánh dấu nếu kết quả trả về trống (`is_empty`) hoặc sai về mặt ngữ nghĩa so với đáp án chuẩn.
- **`x12` (Failure Analysis)**: Khi có lỗi (từ `x10` hoặc `x11`), module phân tích sẽ gán nhãn gốc rễ lỗi (Root Cause Label - ví dụ: `sql_wrong_join`, `schema_missing_column`), đưa ra lý do và gợi ý sửa chữa.
- **`x13` (Repaired SQL)**: Câu SQL mới được sửa dựa trên gợi ý từ `x12`.

### 🔴 Cách So Sánh (Step D Detection)
- Đánh giá xem lỗi nằm ở cú pháp (dựa vào `x10`) hay sai ngữ nghĩa, trả về rỗng (dựa vào `x11`).
- So sánh xem chuỗi chẩn đoán bệnh ở `x12` có khớp với kho phân loại lỗi (Taxonomy) hay không.
- Đối chiếu SQL sửa sai `x13` với SQL ban đầu `x9` để đảm bảo hệ thống có thực sự sửa lỗi và KHÔNG sinh ra thêm một lỗi mới toanh nào khác (regression).
