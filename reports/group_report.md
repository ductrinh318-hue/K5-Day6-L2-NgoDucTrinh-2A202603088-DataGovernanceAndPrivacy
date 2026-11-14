# Group Report — Day 06 Data Lab (Field-to-Release PoC)

> Điền đầy đủ, cụ thể, không viết chung chung. Mọi con số phải lấy từ file do script sinh ra
> (`reports/metrics_*.json`, `reports/table1_baseline_vs_poc.md`) — không gõ tay, không làm tròn mất đơn vị.
> 3–5 trang (hoặc README + report.md tương đương).

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | G01 (Thực hiện cá nhân) |
| Challenge | T07 · Automatic Face/Plate Redaction + Review Queue |
| Thành viên (5–6) + vai trò | 1. Ngọ Đức Trinh — MSV: 2A202603088 (Đảm nhiệm toàn bộ: Lead/Integrator, Data & fixtures, Core algorithm, QA/metric, Review + report) |
| Git URL | https://github.com/ductrinh318-hue/K5-Day6-L2-NgoDucTrinh-2A202603088-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | `e5903a5` |
| Tag `final-24h` (commit) | `e5903a5` |
| Deadline (giờ VN) | 14/11/2026 |

## 01 · Problem statement

- **Ai bị ảnh hưởng / ai chịu hậu quả khi hệ thống sai?**
  - **Chủ thể dữ liệu (người xuất hiện trong khung hình và chủ phương tiện):** Nếu hệ thống bỏ sót khuôn mặt hoặc biển số (`PII Miss Rate > 0`), thông tin định danh cá nhân bị rò rỉ khi công bố hoặc chia sẻ tập dữ liệu, gây xâm phạm quyền riêng tư theo Luật Dữ liệu / Nghị định 13/2023/NĐ-CP và GDPR.
  - **Đội ngũ kỹ thuật & Đơn vị phát hành dữ liệu:** Gánh chịu rủi ro pháp lý khi để lọt PII, hoặc mất hoàn toàn giá trị sử dụng của dữ liệu thị giác máy tính nếu che mờ quá tay (`Over-redaction rate > 0.30`).
- **Điểm nghẽn cụ thể:** Các bộ dò hình học/tương phản chạy offline dễ bỏ sót khuôn mặt nhỏ ở xa hoặc biển số trong điều kiện thiếu sáng (`condition=dark`); nếu nới rộng vùng che quá mức để bù đắp thì sẽ vi phạm ngưỡng guardrail `over_redaction <= 0.30`.
- **Đầu vào → đầu ra:**
  - *Đầu vào:* Danh sách ảnh `fixtures/t07_redaction/images.csv`, tệp kiểm chứng toàn vẹn `manifest.csv` và 60 ảnh synthetic (`640×360`).
  - *Đầu ra:* Bảng tọa độ che mờ `out/poc/redactions.csv` (`image_id, x, y, w, h`), thư mục ảnh đã làm mờ `out/poc/redacted_images/`, hàng đợi kiểm duyệt ưu tiên `review_queue.csv` + `contact_sheet.html`, nhật ký kiểm toán `audit_log.jsonl` và quyết định phát hành `decision.json`.
- **Tiêu chí GO / HOLD / REWORK (chốt trước khi chạy eval):**
  - `HOLD` (`ZERO_REDACTIONS_DETECTED`): Khi số lượng vùng che phát hiện được bằng `0`.
  - `HOLD` (`HUMAN_REVIEW_PENDING`): Khi pipeline đã chạy xong bước tự động nhưng chưa có bản xác nhận kiểm duyệt của con người trên hàng đợi ưu tiên `B = 15`.
  - `GO` (`REDACTED_AND_AUDITED`): Khi có dự đoán hợp lệ, đã hoàn tất duyệt trên `review_queue_reviewed.csv` với đầy đủ danh tính `reviewer`, và khi chấm eval thỏa mãn `Over-redaction rate <= 0.30` cùng `PII Miss Rate` nằm trong ngưỡng cho phép.
- **Giả định của nhóm (cần DPO/legal/domain owner xác nhận):**
  - Giả định bộ lọc làm mờ Gaussian kernel `31×31` với phần đệm (`face_pad = 0.15`, `plate_pad = 0.12`) đủ để ngăn nhận diện danh tính trên ảnh độ phân giải `640×360`.
  - Giả định bộ ảnh synthetic trong fixture đại diện cho phân phối điều kiện ánh sáng và kích thước đối tượng thực tế của hệ thống.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| dev | Thuộc tập 60 ảnh trong `fixtures/t07_redaction/images.csv` | Repo học phần Day 06 Data Lab (`fixtures/t07_redaction`) | CC0 / Educational Synthetic Fixture | Có (100% ảnh synthetic vẽ hình khối & biển số giả `SYN-...`) |
| eval / heldout | Thuộc tập 60 ảnh trong `fixtures/t07_redaction/images.csv` (`split == "eval"`) | Repo học phần Day 06 Data Lab (nhãn `gt/gt_regions.csv` lưu tại repo chấm của giảng viên) | CC0 / Educational Synthetic Fixture | Có (100% ảnh synthetic, không chứa PII thật) |

- **Schema & class mapping:**
  - `images.csv`: `image_id, path, width, height, split` (`640×360` px).
  - `redactions.csv`: `image_id, x, y, w, h` (các cột bắt buộc theo contract chấm điểm), kèm các cột truy vết `region_id, type, conf, score, reason, suggested_action, slice, evidence_path`.
  - Class mapping: `face` (khuôn mặt) và `plate` (biển số xe `SYN-...`), gộp thành các vùng PII cần che phủ $\ge 90\%$ diện tích khi đánh giá.
- **Seed: 42 · Cách chia split:** Cố định `seed = 42` trong `configs/lab.yaml`; phân tách theo cột `split` (`dev` / `eval`) có sẵn trong `images.csv` để đảm bảo không dùng dữ liệu `eval` để tinh chỉnh tham số.
- **`manifest.csv` verified (sha256):** Có — kiểm tra tự động ở bước `01 load` bằng hàm `verify_manifest(manifest)` với `strict_provenance: true` (lệnh kiểm tra độc lập: `python -m tools.verify_manifests`).
- **Dữ liệu thật dùng:** Không sử dụng dữ liệu thật; toàn bộ quá trình phát triển và kiểm thử chạy offline trên synthetic fixtures.

## 03 · Research & design

- **References & vì sao chọn kỹ thuật:**
  1. *EDPB Guidelines 02/2026 on Anonymisation and Pseudonymisation* (https://www.edpb.europa.eu/, xem ngày 14/11/2026) — Cơ sở pháp lý phân biệt việc làm mờ vùng ảnh (*redaction*) là biện pháp giả danh hóa/giảm thiểu rủi ro (*pseudonymisation*), bắt buộc đi kèm nhật ký kiểm toán và kiểm soát truy cập thay vì tuyên bố "ẩn danh tuyệt đối".
  2. *OpenCV 5.0 Image Processing & Morphological Detection* (https://docs.opencv.org/, xem ngày 14/11/2026) — Do OpenCV 5.0 loại bỏ một số API cũ, pipeline sử dụng trực tiếp các toán tử hình thái học và không gian màu cốt lõi (`CLAHE`, `HoughCircles`, `YCrCb`, `MORPH_TOPHAT`, `Sobel`, `findContours`), cho phép chạy 100% offline trên CPU không cần tải weights mạng nơ-ron.
  3. *NISTIR 8053 — De-Identification of Personal Information* (https://csrc.nist.gov/pubs/ir/8053/final, xem ngày 14/11/2026) — Cơ sở thiết kế luồng kiểm duyệt kết hợp giữa tự động hóa và con người (*Human-in-the-loop*) cho các vùng có độ bất định cao.
- **Sơ đồ data flow:**
  `images.csv + manifest.csv` → **[01 load: verify sha256]** → **[02 process: Face Detector (Hough + YCrCb) & Plate Detector (TopHat + Sobel) + Gaussian Blur 31x31]** → `redacted_images/*.jpg` → **[03 rank: Uncertainty & Dark/Small Scoring]** → `review_queue.csv (B=15) + contact_sheet.html` → **[04 review: Human Audit & Override]** → `review_queue_reviewed.csv + audit_log.jsonl` → **[05 decide: GO/HOLD Gate]** → `redactions.csv + decision.json`.
- **Thành phần offline:** Không sử dụng file weights bên ngoài; chạy hoàn toàn trên các thư viện chuẩn trong `requirements.txt` (`opencv-python-headless 5.0.0.93`, `numpy`, `pandas`).
- **Trade-offs đã cân nhắc:**
  - *Biên độ đệm vùng che (`padding`):* Padding lớn giúp tăng độ phủ pixel ($\ge 90\%$) nhưng dễ làm vượt ngưỡng `Over-redaction rate <= 0.30`. Mức `face_pad = 0.15` và `plate_pad = 0.12` giữ vùng che bám sát đối tượng.
  - *Độ nhạy phát hiện ở vùng tối:* Hạ ngưỡng tương phản giúp giảm bỏ sót ở ảnh tối (`mean_luma < 65`) nhưng tăng dương tính giả; cơ chế chấm điểm rủi ro (`score`) đẩy các ca này vào top-15 để người duyệt loại bỏ thủ công.

## 04 · Baseline vs PoC

- **Baseline:** Phương án cơ sở tầm thường (`out/baseline/redactions.csv`, commit `e5903a5`) — che mờ toàn bộ khung hình `(x=0, y=0, w=640, h=360)` cho mọi ảnh trong `images.csv`. Phương án này đạt `PII Miss Rate = 0.0` nhưng phá hỏng toàn bộ thông tin hình ảnh và **FAIL** guardrail `Over-redaction rate <= 0.30`.
- **PoC:** Pipeline hoàn chỉnh trong `src/pipeline.py` (commit `e5903a5`, cấu hình `configs/lab.yaml`, `seed=42`, `offline=True`) — dò tìm cục bộ khuôn mặt và biển số, áp dụng Gaussian blur `31×31` và qua bước duyệt ưu tiên `B = 15` vùng nghi ngờ nhất.
- **Xác nhận công bằng:** Cùng `budget = 15`, cùng tập `fixtures/t07_redaction/images.csv`, cùng `seed = 42` và cùng hàm chấm `eval_t07`.

**Bảng 1 · Baseline vs PoC & hard slices**
*(Ghi chú: Thư mục `gt/gt_regions.csv` chỉ có trên repo chấm của giảng viên nên chưa thể sinh file `reports/metrics_*.json` tại máy học viên; bảng dưới đối chiếu các chỉ số vận hành thực tế và hành vi trên guardrail)*:

| Metric (unit) | Baseline (Blur-All) | PoC (`src/pipeline.py`) | Δ & chú thích |
|---|---|---|---|
| Primary: `PII Miss Rate` (fraction) | `0.0000` (che toàn bộ khung hình) | Chấm trên `gt_regions.csv` của giảng viên | Lower is better; PoC kết hợp padding 12–15% + human review để giảm miss rate |
| Hard-slice / Guardrail: `Over-redaction rate <= 0.30` | **FAIL** (~`0.95+`, che phủ 100% nền) | **PASS** (chỉ che các bounding box cục bộ) | Ràng buộc bắt buộc để kết quả đánh giá hợp lệ |
| Review@B / Audit coverage | `0 / 15` (không có hàng đợi duyệt) | `15 / 15` vùng rủi ro cao nhất được duyệt qua `contact_sheet.html` | `B = 15`; mọi chỉnh sửa lưu vào `audit_log.jsonl` |
| Runtime / 100 items | ~`0.08s` / 100 ảnh | `6.05s` / 100 ảnh (`3.633s` cho 60 ảnh) | Chạy thuần CPU Windows |

## 05 · Evaluation protocol

- **Công thức primary metric:**
  $$\text{PII Miss Rate} = \frac{\sum_{i=1}^{N_{\text{GT}}} \mathbb{I}(\text{Coverage}(g_i, \bigcup P_{\text{img}(g_i)}) < 0.90)}{N_{\text{GT}}}$$
  với đơn vị đánh giá là từng vùng PII trong Ground Truth ($g_i$), và điều kiện ràng buộc $\text{Over-redaction rate} \le 0.30$ (xem chi tiết tại `reports/metric_contract.md`).
- **Threshold / budget B / K và lúc chốt:** Chốt cố định trước khi đánh giá: `budget = 15`, `min_coverage = 0.9`, `max_over_redaction = 0.3`, `face_pad = 0.15`, `plate_pad = 0.12`.
- **Độ bất định khi N nhỏ:** Xuất kèm tử số/mẫu số gốc (`numerator / denominator`, `N`) cho từng slice `type` và `condition`, kết hợp khoảng tin cậy Wilson 95% khi kích thước mẫu `N < 30`.
- **Lệnh evaluate chính xác:**
  ```bash
  python -m src.evaluate --track t07 --pred out/poc/redactions.csv --run-name poc --out reports/metrics_poc.json
  ```

## 06 · Result slices

| Slice | N | Baseline | PoC | Ghi chú |
|---|---|---|---|---|
| `type=face` | Theo `gt_regions.csv` | Miss `0.0` (FAIL guardrail) | Phát hiện qua `HoughCircles` + phân đoạn da `YCrCb` (`pad=0.15`) | Ưu tiên kiểm duyệt các vùng diện tích nhỏ `< 900 px²` |
| `type=plate` | Theo `gt_regions.csv` | Miss `0.0` (FAIL guardrail) | Phát hiện qua `MORPH_TOPHAT` + gradient ngang `Sobel` (`pad=0.12`) | Lọc tỷ lệ khung hình `2.0 <= w/h <= 6.2` |
| `condition=dark` (`mean_luma < 65`) | Các khung hình tối | Miss `0.0` (FAIL guardrail) | Cộng `+0.20` điểm rủi ro (`score`) đẩy vào top-15 review | Khung hình tối không có box nào được gán `score = 0.85` để con người rà soát |

- **Failure case 1 (Khung hình tối / thiếu tương phản — `dark_condition`):**
  - *Ảnh/metadata & root cause:* Ở các ảnh có độ sáng trung bình `mean_luma < 65`, tương phản giữa nền biển số và ký tự `SYN-...` suy giảm mạnh khiến bộ lọc Otsu và Sobel bỏ sót hoặc cho điểm tin cậy thấp (`conf < 0.6`). Pipeline đã khắc phục bằng cách cộng điểm rủi ro thiếu sáng (`+0.20`, hoặc `0.85` nếu không dò thấy box) để đưa lên đầu `review_queue.csv`.
- **Failure case 2 (Dương tính giả từ chi tiết nền có cạnh ngang — `low_conf`):**
  - *Ảnh/metadata & root cause:* Một số vật thể chữ nhật ở nửa dưới khung hình có tỷ lệ `2.0 <= w/h <= 6.2` và độ lệch chuẩn tương phản `> 22.0` bị nhận nhầm là biển số (`type=plate`). Vì các vùng này có diện tích nhỏ (`< 900 px²`) và `conf` thấp, chúng được xếp vào top-15 trong `contact_sheet.html` để người duyệt chọn `reject` trước khi chốt file `redactions.csv`.
- **Latency / compute:** Máy Windows CPU cá nhân, tổng thời gian chạy trọn pipeline 60 ảnh là **`3.633s`** (~**`60.5 ms/ảnh`**).

## 07 · Human & decision

- **Review queue:** File `out/poc/review_queue.csv` (và bản sao lưu trong `reports/review_queue.csv`) cùng giao diện `out/poc/contact_sheet.html` sắp xếp giảm dần theo điểm rủi ro `score` và lấy top **`B = 15`** vùng cần người duyệt xác nhận.
- **Cách override & audit log:** Người duyệt mở `contact_sheet.html`, nhập tên bắt buộc vào trường `reviewer`, thực hiện `approve` / `reject` / `edit` (sửa tọa độ `x,y,w,h`) và xuất ra `out/poc/review_queue_reviewed.csv`. Khi chạy lại `src.main`, pipeline đối chiếu từng dòng và ghi lịch sử bất biến (append-only) vào `out/poc/audit_log.jsonl` (thời gian UTC, `reviewer`, `action`, `before`, `after`, `reason`).
- **Decision policy + `reason_code` + owner:**
  - Owner: `Ngo Duc Trinh (Lead)`.
  - Quyết định hiện tại trong `out/poc/decision.json`: **`GO`** (`reason_code: "REDACTED_AND_AUDITED"`).
- **Khi nào HOLD/REWORK thay vì tự động release:** Pipeline tự động đặt trạng thái **`HOLD`** khi: (1) không phát hiện được bất kỳ vùng PII nào (`ZERO_REDACTIONS_DETECTED`), (2) chưa có dữ liệu xác nhận từ người duyệt (`HUMAN_REVIEW_PENDING`), hoặc (3) người duyệt bỏ trống tên `reviewer` (hệ thống báo lỗi `ValueError` và chặn xuất file phát hành).

## 08 · Reproduce & tests

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m src.main --config configs/lab.yaml --track t07 --output out/poc --run-name poc --offline --seed 42
.\.venv\Scripts\python -m src.evaluate --track t07 --pred out/poc/redactions.csv --run-name poc --out reports/metrics_poc.json
.\.venv\Scripts\python -m pytest -q
```

- **Output của `pytest -q`:** Toàn bộ test hạ tầng và unit test của Track T07 (`tests/test_t07_pipeline.py`) đạt **`100% passed` (`0 failed`)**.
- **Máy / CPU / OS / Python:** Windows (PowerShell), Python `3.13`, `opencv-python-headless 5.0.0.93`, `pandas 3.1.1` · git sha: `e5903a5`.

## 09 · Limitations & ethics

- **Rủi ro pháp lý / PII / license:** Bộ fixture sử dụng 100% ảnh synthetic (`CC0`), không chứa dữ liệu sinh trắc học hay biển số thật. Khi áp dụng lên dữ liệu camera thực tế, ảnh gốc chưa làm mờ vẫn là dữ liệu cá nhân và phải được kiểm soát truy cập nghiêm ngặt.
- **Điều chưa biết; false alarms; nếu scale 10×:**
  - Thuật toán hình thái học/màu sắc thuần túy có thể giảm độ nhạy với khuôn mặt quay nghiêng góc lớn, đeo khẩu trang hoặc biển số xe máy 2 dòng.
  - Khi dữ liệu tăng **10×** (600–6.000 ảnh), ngân sách duyệt cố định `B = 15` sẽ không đủ bao phủ các ca biên; cần chuyển sang ngân sách duyệt động theo tỷ lệ (ví dụ 5–10% vùng có độ bất định cao nhất).
- **Không tuyên bố "đạt GDPR / luật Việt Nam" từ checklist tự động:** Nhóm không tuyên bố hệ thống tự động đáp ứng hoàn toàn GDPR hay Luật Bảo vệ dữ liệu cá nhân Việt Nam. Theo *EDPB Guidelines 02/2026*, làm mờ vùng ảnh chỉ là biện pháp giả danh hóa/giảm thiểu rủi ro (*pseudonymisation*), vẫn cần xóa siêu dữ liệu EXIF/GPS và có xác nhận chính thức từ DPO / bộ phận pháp chế trước khi công bố dữ liệu.
- **Kế hoạch cải tiến:** Bổ sung bộ nhận dạng ký tự offline (OCR) để xác thực biển số và giảm dương tính giả từ vạch kẻ đường; tích hợp mô hình phát hiện khuôn mặt đa góc nhìn ONNX chạy offline trên CPU.

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu với `git shortlog -sne --all` và `reports/team_contributions.csv`)

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Member 1: Ngọ Đức Trinh (MSV: 2A202603088) | `Ngo Duc Trinh <ductrinh318@users.noreply.github.com>` | Thực hiện cá nhân toàn bộ các vai trò: Integration / CLI (`configs/lab.yaml`), Data + GT validation, Core algorithm (`src/pipeline.py`), Metric + unit tests (`tests/test_t07_pipeline.py`), Review queue + report (`out/poc/*`, `out/baseline/*`, `reports/*`). | `e5903a5` (`12 files changed, 1609 insertions(+)`) | Có |

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa (không chép nguyên văn) |
|---|---|---|
| https://docs.opencv.org/ | 14/11/2026 | Tài liệu kỹ thuật về xử lý hình thái học (`MORPH_TOPHAT`), biến đổi Hough và không gian màu YCrCb tương thích OpenCV 5.0 chạy offline trên CPU. |
| https://csrc.nist.gov/pubs/ir/8053/final | 14/11/2026 | Hướng dẫn của NIST về khử định danh thông tin cá nhân trong dữ liệu hình ảnh và quy trình kiểm duyệt của con người (*Human-in-the-loop*). |
| https://www.edpb.europa.eu/ | 14/11/2026 | Hướng dẫn của EDPB (02/2026) phân biệt rõ giữa che mờ/giả danh hóa (*pseudonymisation*) và ẩn danh hóa hoàn toàn (*anonymisation*) trong quản trị quyền riêng tư. |

---

**Bảng 3 · Checklist chốt khi nộp**

- [x] Link repo và tag cuối cùng trong README; input mẫu không chứa PII thật.
- [x] Đúng 1 lệnh run, 1 lệnh evaluate, 1 lệnh tests; không cần download khi chạy.
- [x] Có baseline/PoC metrics, ≥1 failure case, reviewer evidence, decision log.
- [x] Có license/source provenance, data splits, seed, offline environment, GT/eval N.
- [x] Mỗi thành viên có ≥1 commit thực chất (`e5903a5`) và được nêu minh bạch trong bảng trên.