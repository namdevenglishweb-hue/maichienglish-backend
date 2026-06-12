---
feature: exam-ai-generation
type: frontend
status: approved
last-updated: 2026-06-10
author: backend
depends-on: [exam, section, question]
---

# Exam AI Generation — Lưu ý cho Frontend

> Tính năng sinh "đề tương tự" từ 1 đề gốc bằng AI. Phase 2 mở cho FE qua **HTTP API admin-scoped,
> chạy nền (async job) + poll tiến trình**.
>
> **Triết lý doc này:** giải thích *ý nghĩa data & ý đồ* để FE hiểu, **không** lặp lại chi tiết
> request/response — schema xem trực tiếp trên **Swagger**. Render/hiển thị là quyết định của FE.

---

## 1. Role journey

Toàn bộ tính năng là **admin-only** (mọi endpoint `require_admin`). Student/teacher/parent **không** thấy gì cho tới khi admin review + publish đề ra.

Có **2 mode** dùng chung lõi gen, admin chọn theo nhu cầu:

### Mode 1 — Cả đề, server tự lưu (nhanh)
```
1. Chọn đề gốc → 2. POST tạo job (K, title?, sectionPrompts?) → 3. poll job
4. succeeded → resultExamId (đề DRAFT) + report → 5. review/sửa → 6. xử lý media → 7. publish
```
Đề **tự được lưu** cuối job; nếu có part nào fail thì **cả job fail, không tạo đề** (all-or-nothing).

### Mode 2 — Tương tác từng part, admin tự lưu (kiểm soát)
```
1. Chọn đề gốc (GET /api/exams/{id}?include=sections → biết danh sách part + sectionId)
2. Gen từng part   → POST /section (sourceSectionId, K, sectionPrompt?) → poll job
                     → report.sections[0] = part đã gen → FE đưa vào "CỘT PHẢI" (giữ ở client)
   (hoặc) Gen hết 1 lần → POST /preview → poll → report.sections[] = tất cả part
3. Xem/chỉnh/gen lại từng part tuỳ ý (mỗi part độc lập)
4. Đủ part → admin bấm SAVE → POST /assembled-exam (gửi cả cụm part ở cột phải)
            → server validate + tạo đề DRAFT (trả về exam) → 5. review → 6. media → 7. publish
```
FE **làm chủ việc lắp ráp + quyết định lưu**; server chỉ gen từng part và lưu **khi được bảo**.

> Bước review/media/publish (5–7) ở **cả 2 mode** đều **dùng lại** màn soạn đề + endpoint exam hiện hữu — không có màn/endpoint mới cho phần đó.

## 2. Các cách FE có thể bố trí (gợi ý, FE tự quyết UI)

| Cách | Mode | Khi nào hợp |
|---|---|---|
| **Quick generate** | 1 | Nút "Sinh đề tương tự" → popup chọn K → Go, chờ ra đề. Nhanh. |
| **Workbench 2 cột** | 2 | Trái = part đề gốc; phải = part đã gen. Gen từng part / gen hết, xem ở cột phải, chỉnh, rồi Save. |
| **Job center** | 1+2 | Màn danh sách job (poll list) theo dõi tiến trình, lỗi, token, hủy, gen lại. |
| **Quản lý prompt theo type** | — | Màn admin riêng xem/sửa "additional prompt" theo section type (xem §5). |

## 3. Vì sao là async + cách poll cho đúng

- Gen chạy **lâu** (mỗi section: AI sinh + AI tự review 1–2 vòng; cả đề có thể vài phút). Vì vậy **không có** API đồng bộ trả đề ngay — POST chỉ trả **`jobId`** (HTTP 202), việc thật chạy nền.
- FE **poll** `GET job/{jobId}` mỗi ~**2–3s** tới khi `status` ở trạng thái kết thúc.
- **Vòng đời `status`:** `pending → running → (succeeded | failed | aborted)`. Ba cái sau là **terminal** (ngừng poll).
- **Tiến trình:** `sectionsTotal` + `sectionsDone` + `currentSection` → vẽ progress "đang xử lý part 5/7" (Mode 1/preview) hoặc "1/1" (gen 1 part). `sectionsTotal` có thể null lúc `pending`.
- **Kết quả theo `scope`:** `scope=exam` → `resultExamId` (đề đã lưu). `scope=section`/`exam_preview` → `report.sections[]` (part payload, **chưa lưu**) → FE đưa vào cột phải, giữ ở client tới khi Save.
- **Ngoại lệ — Save (Mode 2):** `POST /assembled-exam` **không gọi AI** nên **đồng bộ**, trả đề mới ngay (không qua job, không poll). Chỉ các tác vụ *gen* mới async.

## 4. Ý nghĩa data quan trọng (Swagger không nói hết)

### 4.1 Xử lý lỗi khác nhau theo mode
- **Mode 1 (`scope=exam`)**: **all-or-nothing** — 1 part không đạt ⇒ `failed`/`aborted`, **KHÔNG tạo đề**. FE đừng giả định "thành công một phần"; đọc `abortedReason` (vd *"section 5: self-review còn lỗi critical"*) → cho admin gen lại (đổi K/prompt/model).
- **Mode 2 single (`scope=section`)**: chỉ gen 1 part — fail thì **chỉ part đó fail**, admin gen lại riêng part, không ảnh hưởng các part khác đã ở cột phải.
- **Mode 2 preview (`scope=exam_preview`)**: trả **per-part status** trong `report.sections[]` (part nào `ok`, part nào `failed`) — part fail KHÔNG chặn part khác; admin gen lại riêng part fail rồi mới Save.
- **Save (`/assembled-exam`)**: không ép "đủ N part như đề gốc"; nếu FE gửi shape part hỏng → 400 (BE validate lại). Có thể trả `warning` nếu số part < đề gốc (FE hiển thị, không chặn).

### 4.2 `resultExamId` là một đề **DRAFT** (`isPublished=false`)
Đề sinh ra **cùng cấu trúc** đề gốc (đúng số section/câu/loại/số option) — chỉ **nội dung** khác. ⇒ FE render y như một đề thường, **không cần loại UI mới**. Luôn ở draft vì cần người review (AI có thể sai đáp án; media chưa thay). FE **đừng auto-publish**.

### 4.3 Media meta đi theo material (`transcript`/`description`) — quan trọng
Audio/ảnh trong `materials` mang thêm `meta`:
- **audio** → `meta.transcript` (đoạn nghe dạng text), **image** → `meta.description` (mô tả ảnh).
- `meta.pendingReplacement = true` ⇒ **file media vẫn là file CŨ** của đề gốc, **admin phải thay thủ công** (thu/upload audio hoặc ảnh mới khớp transcript/description) trước khi publish.
- `meta.transcript` chính là **transcript gợi ý** để admin tạo audio mới (và là nguyên liệu cho feature tạo audio sau này). FE hiển thị transcript cạnh material để admin thu cho khớp.

> ⚠️ **Bảo mật:** `material.meta` (nhất là `transcript`) = **đáp án bài nghe** → BE **strip khỏi payload học sinh**. FE **chỉ** thấy/sửa meta trong **màn soạn đề của admin**, tuyệt đối không hiển thị cho student.

### 4.4 `report` — các field còn lại
- **`mediaTodos[]`** — snapshot tiện tra các material cần thay (`{sectionPosition, materialIndex, mediaType, reason}`); nguồn-sự-thật là `meta.pendingReplacement` trên chính material. FE nên **chặn publish** khi còn material `pendingReplacement=true`.
- **`selfReview{}`** — mỗi section: số vòng AI tự review + issue còn lại. Hiển thị để admin đánh giá chất lượng.
- **`tokenUsage`, `retries`** — kiểm toán chi phí; hiển thị tuỳ nhu cầu.

### 4.5 Đề gốc & đề mới liên kết qua `generatedFromExamId`
Đề mới mang `generatedFromExamId` = id đề gốc (badge "AI-generated" / link ngược). `null` ⇒ đề tạo tay.

### 4.6 Đề gốc PHẢI có meta cho mọi audio/ảnh — nếu thiếu sẽ bị từ chối
AI **không nghe được audio / không thấy ảnh**, nó dựa vào `meta.transcript`/`meta.description` của **đề gốc** làm nguyên liệu. Vì vậy **đề gốc thiếu meta ⇒ KHÔNG dùng để gen được**:
- POST tạo job sẽ trả **400** liệt kê section/material còn thiếu transcript/description (BE check trước khi tạo job, không tốn token).
- ⇒ FE nên **pre-check phía client**: trước khi cho bấm "Sinh đề tương tự", quét đề gốc xem mọi audio có `meta.transcript`, mọi image có `meta.description` chưa; thiếu thì **chỉ ra material nào** và dẫn admin tới màn soạn đề để điền, thay vì để POST fail.
- Đề gốc cũ (chưa có meta) cần admin **bổ sung transcript/description** trước; đây cũng chính là dữ liệu nuôi feature tạo audio sau này.

## 5. Hai cách admin "dặn" AI: per-type (config) + per-section (lúc gen)

Admin có **2 nguồn** prompt bổ sung, **cộng dồn**, phục vụ 2 mục đích khác nhau (đều chỉ là *gợi ý* — BE đảm bảo không phá cấu trúc/đáp án):
- **(A)** = hướng dẫn **nghiệp vụ chung** cho từng loại section (ổn định, lâu dài).
- **(B)** = **ý tưởng nội dung** cho riêng lần gen này (nhất thời).

### 5.1 (A) Per-type — config bền (hướng dẫn nghiệp vụ)
- Admin lưu 1 prompt cho **mỗi section type** (`multiple_choice`, `multiple_choice_shared`, `fill_blank`, `matching`, `writing`, `speaking`, `form_completion`). Áp cho **mọi** section cùng type, **mọi** lần gen.
- FE: màn quản trị **list / upsert(theo type) / delete** (CRUD endpoint admin — xem Swagger).
- Bảng rỗng là hợp lệ. Dùng cho ý đồ ổn định, lâu dài (vd "dạng KET Part 2 nên dựng bảng thế nào").

### 5.2 (B) Per-section — ad-hoc, ngay lúc generate (ý tưởng nội dung) ⭐
- Trong **dialog gen** (sau khi chọn đề gốc), FE hiển thị **danh sách section của đề gốc** và cho admin nhập **prompt riêng cho từng section** (tuỳ chọn — section nào muốn thì nhập).
- Gửi kèm trong body POST tạo job: `sectionPrompts = { "<sourceSectionId>": "dặn riêng cho section này..." }` — **keyed theo `sectionId` của đề gốc** (lấy từ `GET /api/exams/{id}?include=sections`).
- **Chỉ áp cho lần gen này**, không lưu lại. Dùng cho ý đồ nhất thời ("lần này section 3 đổi sang chủ đề du lịch giúp tôi").
- Khi (A) và (B) cùng có và mâu thuẫn → **(B) thắng** (cụ thể hơn), nhưng cả hai vẫn dưới ràng buộc cấu trúc/đáp án.

> Phân biệt: **(A)** = theo *loại* & *bền*; **(B)** = theo *section cụ thể* & *nhất thời*. FE nên để (B) ngay trong luồng gen, (A) ở màn cấu hình riêng.

## 6. Lưu ý integrate khác

- **Validate phía client trước khi POST:** `k` ∈ [1,5]; đề gốc nên là đề **đã có câu hỏi** (BE từ chối đề rỗng bằng 400/404).
- **Cho phép gen nhiều lần** từ cùng 1 đề gốc — mỗi lần là 1 job độc lập. FE không cần chặn trùng (nhưng có thể nhắc admin về chi phí token).
- **Hủy job:** chỉ *cooperative* — BE dừng **giữa các section**, không cắt giữa một bước AI. Sau khi bấm hủy, status sẽ chuyển `aborted` ở ranh giới section kế tiếp (không tức thì). Hủy job đã xong ⇒ vô hiệu.
- **Job kẹt `running`:** nếu server restart giữa chừng, job có thể kẹt ở `running` (BE chưa auto-resume ở v1). FE nên coi job `running` mà **`updatedAt` quá lâu** (vd >15') là *stale* → hiển thị cảnh báo + gợi ý gen lại, thay vì poll vô hạn.
- **Đáp án cần admin kiểm lại:** AI tự review nhưng không đảm bảo 100% đúng ngữ nghĩa ⇒ FE nên nhấn mạnh bước review trước publish.

## 7. Chọn model AI (CẬP NHẬT 2026-06-10)

Admin có thể **chọn model** mà không cần redeploy. Hai tầng:

**(1) Override per-request — chọn model cho riêng lần gen này.**
- Trong dialog gen, FE thêm dropdown **Provider + Model** (tuỳ chọn). Gửi kèm body POST: `aiModel`, `aiProvider`.
- Bỏ trống ⇒ dùng config mặc định (tầng 2) → env.
- Provider hợp lệ: `openrouter` (slug vd `anthropic/claude-sonnet-4.5`, `google/gemini-2.5-flash`), `groq` (vd `llama-3.3-70b-versatile`), `gemini` (direct, vd `gemini-3-flash-preview`), `anthropic`. Provider lạ → **400**.
- Lấy danh sách model cho dropdown: `GET /api/admin/exam-generations/models?provider=<...>`.

**(2) Config mặc định — `GET/PUT /api/admin/ai-settings`** (màn "AI Settings" cho admin):
- `GET` → `{effective, stored}`: `effective` = đang dùng thật; `stored` = override đã lưu (null = theo env).
- `PUT` body subset `{provider?, model?, maxTokens?, selfReviewRounds?}`: sửa field nào gửi field đó; **gửi `null` để xoá** (về env). Đổi mặc định toàn hệ thống, **không cần deploy**.
- Ví dụ chuyển sang Gemini: `{provider:"gemini", model:"gemini-3-flash-preview", selfReviewRounds:0}` (Gemini flash làm giám khảo chưa ổn → tắt self-review).

> **Thứ tự ưu tiên:** override per-request (1) > config DB (2) > env. `generationMeta.model`/`.provider` của đề đã gen ghi **model THẬT** đã dùng (để truy vết).
