---
feature: exam-image-generation
type: design
status: approved
last-updated: 2026-06-07
author: backend
depends-on: [exam-ai-generation, exam, section]
---

# Exam Image Generation — Auto-sinh ảnh cho material (Design / Core feature)

> Khi gen đề (docs/exam-ai-generation/), mỗi image material nhận `meta.description` mới + cờ
> `pendingReplacement=true` (file ảnh vẫn là ảnh cũ). Feature này **tự sinh ảnh thật** từ
> `meta.description` qua **OpenRouter** (model image-output, vd `google/gemini-2.5-flash-image-preview`
> = Nano Banana), vision-verify khớp mô tả, upload Supabase, trả URL ảnh để admin không phải tự làm tay.
>
> **Mô hình:** **FE-driven, per-image, job-based** — backend chỉ lo **1 ảnh / job**; FE bắn **N job
> cho N ảnh** (đúng pattern Mode 2 của exam-gen). Endpoint **stateless**: nhận `description` → trả
> URL ảnh (`resultUrl`); FE tự gắn vào material.
>
> **Off mặc định:** chỉ chạy khi `IMAGE_GENERATION_ENABLED=true`. Là **subsystem riêng**, cắm thêm,
> không sửa luồng exam-gen hiện có.

---

## 1. Mục tiêu & sub-features (B1)

| # | Sub-feature | Mô tả |
|---|---|---|
| 1 | Per-image core | `generate_one_image(description, source_image_url?, exam_context?) → {image_url, mode, rounds, usage}`: sinh/chỉnh ảnh → vision-verify → upload. Lõi tái dùng (single); raise `ImageGenerationError` khi không đạt. |
| 2 | Edit-or-generate | Có `sourceImageUrl` (ảnh cũ) ⇒ **EDIT** theo description (giữ layout/chữ); không có ⇒ **generate** from scratch (§3). |
| 3 | **Vision-verify + fallback** | AI vision soi ảnh có khớp mô tả + (functional) đúng chữ/số không; **fail ⇒ KHÔNG nhét ảnh sai** → job failed → FE giữ `pendingReplacement=true` (làm tay). |
| 4 | Job + poll | Mỗi ảnh = 1 job async (BackgroundTasks); FE poll. Per-image độc lập (không all-or-nothing). |
| 5 | Server-side upload | `storage_service.upload_bytes()` đẩy ảnh sinh ra lên bucket `images` (service-role). |
| 6 | Config gate | `IMAGE_GENERATION_ENABLED` (off mặc định) + provider/model/key qua env. |

**Không thuộc phạm vi:** FE batch orchestration (FE tự loop); auto-resume job; sinh audio (TTS — feature riêng); sửa luồng exam-gen.

---

## 2. Kiến trúc — lõi + FE-driven

```
FE: với mỗi ảnh pendingReplacement=true → POST image-gen (N lần) → poll → gắn url
        │
        ▼
api/image_generations/routes.py     admin_router (require_admin)
        │  POST → tạo image_generation_jobs (pending) → BackgroundTasks → 202 {jobId}
        ▼
services/image_job_service.py        run_image_job: mark_running → generate_one_image → finish(succeeded: result_url+report | failed: report.verifyReason)
        ▼
services/image_generation_service.py generate_one_image(...)  ← LÕI (§3)
        ▼
services/ai/image_generator.py       ImageGenerator (ABC) + get_image_generator()  (factory theo IMAGE_PROVIDER)
   • generate_image(description, exam_context) -> (bytes, mime)
   • edit_image(source_url, description, exam_context) -> (bytes, mime)
   • verify_image(image_bytes, description) -> {is_acceptable, reason}
        ▼
services/ai/adapters/openrouter_image.py  openai SDK → OpenRouter (IMAGE_MODEL gen/edit + IMAGE_VERIFY_MODEL verify)
        ▼
services/storage_service.py          upload_bytes(bucket="images", content_type, data) -> public_url
```

**Tách tầng** y như storage + exam-gen adapter: service không biết provider; đổi provider/model chỉ qua env. **FE loop** ⇒ backend KHÔNG cần endpoint batch.

---

## 3. Per-image pipeline (lõi `generate_one_image`) — mirror `generate_one_section`

```
input: description (+ source_image_url? + exam_context?)
mode = "edit" nếu có source_image_url, else "generate"
attempts = max(1, IMAGE_VERIFY_ROUNDS)        # LUÔN sinh ≥1 lần (ROUNDS=0 ⇒ 1 attempt, tắt verify)

for vòng in 1..attempts:
    img, mime = adapter.edit_image(source_url, description, ctx)   nếu mode=edit
              | adapter.generate_image(description, ctx)           nếu mode=generate
    nếu IMAGE_VERIFY_ROUNDS == 0:                            # verify tắt → tin ảnh, không soi
        return _result(img, mime, mode, rounds=0)
    verdict = adapter.verify_image(img, description)         # vision (§5)
    nếu verdict.is_acceptable:
        return _result(img, mime, mode, rounds=vòng)
    # else: retry vòng sau, prompt kèm verdict.reason

hết budget (vẫn không đạt) ⇒ raise ImageGenerationError(reason)
   → job 'failed' → FE giữ pendingReplacement=true (làm tay)

# _result(img, mime, mode, rounds):
#   url = storage.upload_bytes("images", mime, img)
#   return {"image_url": url, "mode": mode, "rounds": rounds, "usage": adapter.usage}
```
> **Lõi trả về `{image_url, mode, rounds, usage}`** (không chỉ url) — `run_image_job` lấy `result_url` + dựng `report` (mode/rounds/usage) từ đây. Raise ⇒ job 'failed', `report.verifyReason` = lý do.
> `IMAGE_VERIFY_ROUNDS=0` = tắt verify (sinh đúng 1 lần, tin ngay); `=N≥1` = tối đa N lần (sinh→verify), retry khi verify từ chối.

Cùng triết lý 2 tầng của exam-gen: **AI vision tự soi** (như self-review) + **fallback an toàn** (không nhét ảnh sai). Per-image **độc lập** — không có all-or-nothing.

---

## 4. OpenRouter image adapter (via openai SDK)

> Toàn bộ AI đi qua **OpenRouter** (OpenAI-compatible, 1 key, nhiều model — xem memory `ai-via-openrouter`). Image gen dùng **model image-output** (vd `google/gemini-2.5-flash-image-preview` = Nano Banana), verify dùng **model vision** — đều là slug OpenRouter từ env.

`services/ai/adapters/openrouter_image.py` (SDK `openai` + `base_url=OPENROUTER_BASE_URL`):
- **generate_image:** `chat.completions.create(model=IMAGE_MODEL, messages=[{user: prompt}], extra_body={"modalities":["image","text"]})` → ảnh là **base64 data URL** trong `message.images` → parse `(bytes, mime)`.
- **edit_image:** message content gồm `{type:text}` + `{type:image_url, image_url:{url: source_url}}` + `modalities image` → trả `(bytes, mime)`. **Thế mạnh** (giữ layout/chữ cho ảnh chức năng).
- `mime` lấy từ data URL; phải ∈ MIME hợp lệ bucket `images` (png/jpeg/webp). Trả mime để upload đặt **đúng đuôi + qua validate** (§6).
- **verify_image:** `chat.completions.create(model=IMAGE_VERIFY_MODEL, messages=[text + image data URL], tools=[report_image_review], tool_choice)` → `{is_acceptable, reason}` (OpenAI tool-calling).
- Adapter giữ `usage` (best-effort: token verify + số ảnh sinh — ảnh tính theo *số ảnh*, không token).
- Model: `IMAGE_MODEL` (gen) + `IMAGE_VERIFY_MODEL` (verify); key `OPENROUTER_API_KEY`; thiếu key ⇒ `RuntimeError` sớm.

---

## 5. Vision-verify checklist (verify_image)

- **Khớp mô tả:** ảnh đúng nội dung `description` (vật/cảnh/người).
- **Đúng dữ kiện chức năng:** nếu mô tả ngụ ý chữ/số mà câu hỏi cần (giờ, giá, tên, SĐT trên form/biển báo) → ảnh phải chứa **đúng** + **đọc được**. Đây là chốt chặn quan trọng nhất (ảnh sai 1 số ⇒ đáp án sai).
- **Chất lượng đề thi thật:** rõ ràng, không méo/lỗi, đúng phong cách level.
- `is_acceptable=false` + `reason` cụ thể ⇒ retry (vòng sau prompt kèm reason) hoặc fallback manual.

> Vì mô hình sinh ảnh **không đáng tin 100% với text nhỏ/chính xác**, vision-verify + fallback là bắt buộc để giữ bất biến "material ↔ question đúng".

---

## 6. Server-side upload

`storage_service.StorageService` thêm:
```python
async def upload_bytes(self, bucket: str, content_type: str, data: bytes) -> str:
    """Upload server-generated bytes → trả public_url. UUID path, ext từ content_type."""
```
`content_type` lấy từ adapter (§4), phải thuộc MIME hợp lệ của bucket (`ALLOWED_TYPES["images"]` = png/jpeg/webp; ≤10MB — tái dùng hằng số `storage_service`); đuôi file suy từ `content_type` (không tin tên). Supabase adapter: dùng `storage3` SDK (service-role) qua `asyncio.to_thread` (không block loop), giống signed-upload hiện có. Bucket `images` đã tồn tại + RLS read (migration 0009).

---

## 7. Job + API (admin-only)

| Method | Path | Vai trò |
|---|---|---|
| `POST` | `/api/admin/image-generations` | Body `{description, sourceImageUrl?, examContext?}` → **202** `{jobId, status}`. `examContext` = `{level, skill}` (optional — style hint). |
| `GET` | `/api/admin/image-generations/{jobId}` | status + `resultUrl` + `mode` + report (FE poll). |
| `GET` | `/api/admin/image-generations` | List job (phân trang, lọc status) cho Job center. |

- **`require_admin`** mọi endpoint.
- **Config gate:** `IMAGE_GENERATION_ENABLED=false` ⇒ **POST** trả **409** `"image generation is disabled"` (không tạo job). GET (poll/list job cũ) vẫn cho phép.
- **Stateless:** chỉ nhận `description` (+ ảnh gốc) → trả URL ảnh (`resultUrl`). **FE tự gắn** vào material (staging hoặc `PUT /api/sections/{id}`) — dùng được cho cả đề-đã-lưu lẫn đề-đang-staging.
- **Không có cancel** (khác exam-gen): job ảnh ngắn (~10–25s) ⇒ FE để chạy xong hoặc bỏ qua `resultUrl`; không cần cooperative-cancel.
- Validation: `description` non-empty (400/422); thiếu `GEMINI_API_KEY` khi enabled ⇒ adapter RuntimeError → job 'failed' (lý do trong report).

Job status response (camelCase — report cũng camelCase, lưu camelCase trong DB):
```jsonc
{ "jobId":"...", "status":"pending|running|succeeded|failed", "mode":"edit|generate",
  "resultUrl":null, "report":{ "rounds":1, "verifyReason":null, "usage":{} },
  "createdAt":"...", "finishedAt":null }
```
`succeeded` ⇒ `resultUrl`. `failed` ⇒ `report.verifyReason` (FE giữ pendingReplacement, làm tay).

---

## 8. DB — migration 0021 (additive, idempotent)

> **0021 đang free** (project hiện tới `0020_exam_generation_jobs`); vẫn verify lại lúc implement phòng feature khác chen vào.

```sql
CREATE TABLE IF NOT EXISTS public.image_generation_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    description       text NOT NULL,
    source_image_url  text,                         -- có ⇒ mode=edit
    mode              text NOT NULL DEFAULT 'generate'
                        CHECK (mode IN ('generate','edit')),
    status            text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','succeeded','failed')),
    result_url        text,
    report            jsonb,
    created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);
CREATE INDEX IF NOT EXISTS image_generation_jobs_status_idx
  ON public.image_generation_jobs (status, created_at DESC);
ALTER TABLE public.image_generation_jobs ENABLE ROW LEVEL SECURITY;
```
**Độc lập exam** — không cần `source_exam_id` (image-gen chỉ dựa vào description). schema.sql cập nhật lockstep.

---

## 9. Config

`config/settings.py` thêm:
| Biến | Default | Ý nghĩa |
|---|---|---|
| `IMAGE_GENERATION_ENABLED` | `false` | Bật/tắt feature (off mặc định). |
| `IMAGE_PROVIDER` | `openrouter` | Chọn adapter qua factory. |
| `OPENROUTER_API_KEY` | `None` | Bắt buộc khi provider=openrouter; thiếu ⇒ adapter RuntimeError. (Dùng chung với text gen.) |
| `IMAGE_MODEL` | `google/gemini-2.5-flash-image-preview` | Slug OpenRouter, model image-output. |
| `IMAGE_VERIFY_MODEL` | `google/gemini-2.5-flash` | Slug OpenRouter, model vision cho verify. |
| `IMAGE_VERIFY_ROUNDS` | `2` | Số lần sinh+verify tối đa/ảnh (§3). **0 = tắt verify** (sinh 1 lần, tin ngay). |

`.env.example` + `requirements.txt` (`openai` — client OpenAI-compatible cho OpenRouter) thêm tương ứng.

---

## 10. Bảng Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|---|---|---|---|
| 1 | Ai loop | **FE-driven (FE bắn N job)** | Backend batch loop | Nhất quán Mode 2; backend gọn (1 ảnh/job, stateless); FE tự quản concurrency/progress/retry. |
| 2 | Thực thi | **Job + poll** | Sync trả thẳng | Sinh ảnh + verify + upload ~10–25s ⇒ tránh timeout Render free; nhất quán hạ tầng job. |
| 3 | Gen mode | **Auto: edit-if-source-else-generate** | Luôn generate / luôn edit | Edit giữ layout/chữ (an toàn ảnh chức năng — thế mạnh Nano Banana); generate cho ảnh mới hẳn. |
| 4 | Quality gate | **Vision-verify + fallback manual** | Tin ảnh sinh ra | Mô hình sinh ảnh không đáng tin với text chính xác ⇒ giữ bất biến đúng-đề; fail không nhét ảnh sai. |
| 5 | Candidates | **1 ảnh + verify + retry** | N ảnh + AI chọn tốt nhất | Rẻ/đơn giản; retry (kiểu self-review) đủ tốt; N-candidates tốn chi phí gấp N. |
| 6 | Quan hệ exam | **Subsystem riêng, stateless theo description** | Nhúng vào job exam-gen | Image-gen không cần exam_id; tách bảng/job riêng, cắm thêm, không sửa luồng cũ. |
| 7 | Gate feature | **`IMAGE_GENERATION_ENABLED` off mặc định** | Luôn bật | Tốn chi phí API + cần key; admin chủ động bật. |

---

## 11. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| Ảnh chức năng (chữ/số) sai ⇒ đáp án sai | **edit ảnh gốc** giữ chữ + **vision-verify** dữ kiện + **fallback manual** (§3, §5) |
| Mô hình render text kém | ưu tiên edit; verify đọc-được; fail → manual |
| Sinh ảnh chậm ⇒ timeout | **job + poll** (không sync) |
| Tốn chi phí API | off mặc định; 1 ảnh + retry (không N-candidates); log `usage` vào report |
| Lộ ảnh sai vào đề | đề vẫn **draft** + admin review trước publish; verify chặn; fail giữ pendingReplacement |
| Thiếu `OPENROUTER_API_KEY` | adapter RuntimeError sớm → job failed có lý do rõ |
| Ảnh upload xong nhưng FE không gắn (orphan trong bucket) | chấp nhận v1 (ảnh thừa vô hại); `pendingReplacement` vẫn true ⇒ admin gen lại; dọn orphan để dành job bảo trì sau |
| FE bắn quá nhiều job song song | FE tự throttle; backend mỗi job độc lập (BackgroundTasks) |
