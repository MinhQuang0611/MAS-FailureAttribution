import json
import os

# File JSON tổng
json_file = "output_logs\\raw_logs\\resume_checkpoint.json"  # đổi thành tên file của bạn

# ID bắt đầu cần xóa
START_ID = 5550

# Đọc dữ liệu JSON
with open(json_file, "r", encoding="utf-8") as f:
    data = json.load(f)

filtered_data = []

for item in data:
    try:
        item_id = int(item.get("id", -1))

        if item_id >= START_ID:
            file_path = item.get("path")

            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, "w", encoding="utf-8") as fw:
                        fw.write("")

                    os.remove(file_path)

                    print(f"Deleted: {file_path}")

                except Exception as e:
                    print(f"Cannot delete {file_path}: {e}")

            continue

        # Giữ lại item không bị xóa
        filtered_data.append(item)

    except Exception as e:
        print(f"Error processing item: {e}")

# Ghi đè lại file JSON
with open(json_file, "w", encoding="utf-8") as f:
    json.dump(filtered_data, f, indent=2, ensure_ascii=False)

print("Done.")