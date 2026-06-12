---
feature: exam-image-generation
type: frontend
status: approved
last-updated: 2026-06-07
author: backend
depends-on: [exam-ai-generation, exam]
---

# Exam Image Generation — Lưu ý cho Frontend

> Auto-sinh ảnh cho image material của đề AI-generated. **Admin-only**, **FE-driven**: FE bắn **N job
> cho N ảnh**, mỗi job 1 ảnh (job-based + poll, đúng pattern Mode 2 của exam-gen).
>
> **Triết lý doc này:** giải thích *ý nghĩa data & ý đồ*, **không** lặp lại request/response — schema
> xem trực tiếp trên **Swagger**.

---

## 1. Role journey (admin-only)

Toàn bộ feature là **admin-only**. Tiếp nối luồng exam-gen: sau khi có đề draft (Mode 1 hoặc staging Mode 2), các image material đang ở trạng thái `meta.pendingReplacement=true` (ảnh vẫn là ảnh cũ, kèm `meta.description` mô tả ảnh mới cần).

```
1. Mở đề draft (có sẵn các ảnh pendingReplacement=true + meta.description)
2. Với mỗi ảnh cần thay:
   FE đọc meta.description (+ url ảnh cũ) → POST image-gen job → poll job
   → succeeded: nhận resultUrl → FE gắn vào material (url=resultUrl, pendingReplacement=false)
   → failed:    FE giữ pendingReplacement=true → admin làm ảnh tay như cũ
3. (FE bắn nhiều job song song cho nhiều ảnh — N spinner) → review đề → publish
```

> Feature **chỉ chạy khi backend bật** `IMAGE_GENERATION_ENABLED`. Khi tắt, POST trả **409** → FE ẩn/disable nút "tự sinh ảnh".

## 2. Cách FE tổ chức (gợi ý, FE tự quyết UI)

- **Per-image button:** mỗi ảnh pendingReplacement có nút "Sinh ảnh" → 1 job. Admin chọn ảnh nào muốn.
- **"Sinh hết ảnh":** FE **tự loop** — bắn N job song song cho N ảnh, hiển thị N spinner. **Đây chính là "batch"** — không có API batch ở backend, FE điều phối.
- **Job center** (tuỳ chọn): theo dõi/gen-lại từng job ảnh (gen-lại = POST job mới; **không có cancel** — job ngắn).

## 3. Vì sao job + poll (không sync)

Sinh 1 ảnh = edit/generate (OpenRouter image model) + vision-verify + upload ≈ **10–25s** ⇒ **không** có API đồng bộ trả ảnh ngay. POST chỉ trả **`jobId`** (202); FE **poll** `GET job/{jobId}` (~2–3s) tới khi terminal.
- **Vòng đời `status`:** `pending → running → (succeeded | failed)`.
- FE bắn N job độc lập ⇒ poll N (hoặc poll list). Mỗi ảnh hỏng/chậm không ảnh hưởng ảnh khác.

## 4. Ý nghĩa data quan trọng (Swagger không nói hết)

### 4.1 `meta.description` là **prompt nguồn** (admin-only)
Mỗi image material AI-generated mang `meta.description` = mô tả ảnh mới cần. **FE gửi chính nó** làm prompt khi tạo job. Là field **admin-only** (đã strip khỏi payload student) → chỉ dùng/hiển thị trong màn soạn đề.

### 4.2 Gửi kèm `sourceImageUrl` (ảnh cũ) ⇒ chất lượng tốt hơn
Nếu FE gửi kèm **url ảnh cũ** của material, backend sẽ **EDIT** ảnh đó theo description (giữ layout/chữ — an toàn cho ảnh form/biển báo) thay vì vẽ mới. → **FE nên luôn gửi `sourceImageUrl`** nếu material đã có ảnh.

### 4.3 `failed` ⇒ KHÔNG có ảnh — giữ pendingReplacement
Nếu vision-verify không đạt sau retry, job `failed` (kèm `report.verifyReason`). **Backend KHÔNG nhét ảnh sai.** FE giữ `material.meta.pendingReplacement=true` → admin làm ảnh tay. FE đừng coi failed là lỗi hệ thống — đó là "không tự làm được, cần người".

### 4.4 `resultUrl` là ảnh đã upload sẵn (Supabase `images`)
`succeeded` ⇒ `resultUrl` là URL ảnh **đã** nằm trong bucket `images` (public). FE chỉ cần **gắn** `material.url = resultUrl` + `meta.pendingReplacement = false`. Không cần upload lại.

### 4.5 Endpoint **stateless** — FE quyết gắn vào đâu
Backend chỉ nhận `description` → trả `resultUrl`, **không** tự sửa đề. FE tự áp:
- Đề **đang staging** (Mode 2 chưa lưu) → set vào object trong "cột phải".
- Đề **đã lưu** (Mode 1 draft) → cập nhật materials của section qua endpoint có sẵn **`PUT /api/sections/{id}`** (SectionUpdate mang `materials`).

## 5. Lưu ý integrate khác

- **Ảnh chức năng vs trang trí:** ảnh chứa chữ/số mà câu hỏi phụ thuộc (form, biển báo, thông báo) rủi ro cao hơn ảnh minh hoạ. Vision-verify + fallback đã chặn, nhưng FE nên **nhắc admin review kỹ** các ảnh chức năng sau khi tự sinh.
- **Đề luôn draft tới khi admin publish** — ảnh tự sinh không auto-publish. Nhấn mạnh bước review.
- **Throttle:** FE tự giới hạn số job song song nếu cần (tránh bắn 50 job 1 lúc). Backend mỗi job độc lập.
- **Feature off:** nếu `IMAGE_GENERATION_ENABLED=false`, POST → 409; FE ẩn nút.
- **Chi phí:** mỗi ảnh tốn API (tính theo số ảnh, không theo token); FE có thể hiển thị `report.usage`/cảnh báo để admin kiểm soát.
