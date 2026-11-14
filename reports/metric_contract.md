# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, threshold hay trọng số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm | 
| ----- | ----- | 
| Track | T07 · Automatic Face/Plate Redaction + Review Queue | 
| Primary metric (tên đúng như evaluator in ra) | `PII Miss Rate` (kèm ràng buộc `Guardrail: Over-redaction rate <= 0.3`) | 
| Tăng tốt hay giảm tốt? | `lower` (càng thấp càng tốt; mục tiêu tiến gần 0.0 mà không vi phạm guardrail) | 
| Đơn vị đánh giá (image / region / pair / release / batch / case …) | `region` (từng vùng khuôn mặt hoặc biển số PII trong Ground Truth) | 
| Công thức + numerator / denominator | `PII Miss Rate = (Số vùng GT có tỷ lệ che phủ pixel < 0.90) / (Tổng số vùng GT trong split eval)`. Guardrail: `Over-redaction rate = (Số pixel bị che nằm ngoài toàn bộ vùng GT) / (Tổng số pixel bị che)`. | 
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) | Tính tổng hợp (union) tất cả các khung dự đoán `(x, y, w, h)` thuộc cùng `image_id`, sau đó đo tỷ lệ diện tích giao giữa vùng GT và tập khung dự đoán (`pixel_coverage >= 0.90`). | 
| Candidate pool cố định | 60 ảnh synthetic trong `fixtures/t07_redaction/images.csv` (lọc theo `split == "eval"` khi chấm chính thức). | 
| Review budget B / K | `B = 15` vùng ưu tiên cao nhất trong hàng đợi duyệt (`review_queue.csv`). | 
| Seed | `42` | 
| Threshold policy (chọn trên dev như thế nào) | Chốt cố định trên `dev` trước khi eval: `min_coverage = 0.9`, `face_pad = 0.15`, `plate_pad = 0.12`, `blur_kernel = 31`, `max_over_redaction = 0.3`. | 
| Eval split (không dùng để tune) | `split == "eval"` trong `fixtures/t07_redaction/images.csv` (không có nhãn `gt/` trong student repo, giữ nguyên tách biệt hoàn toàn). | 
| Chỉ số lỗi xấu (miss / false alarm / false reject) | `PII Miss Rate` (lọt PII chưa che đủ 90%), `Over-redaction rate` (che lẹm quá 30% diện tích nền), và `Region recall = 1 - PII Miss Rate`. | 
| Slices sẽ báo cáo (≥2) | 1\. Theo loại PII (`type=face`, `type=plate`); 2. Theo điều kiện ánh sáng/góc nhìn (`condition=normal`, `condition=dark`, `condition=small`/`angled`). | 
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) | Báo cáo kèm tử số/mẫu số gốc (`num/den`, cỡ mẫu `N`) cho từng slice `type` và `condition`, kết hợp khoảng tin cậy nhị thức Wilson 95% khi `N < 30`. | 
| Ngày giờ chốt + người chốt | 2026-11-14 — Ngọ Đức Trinh (MSV: 2A202603088) | 
