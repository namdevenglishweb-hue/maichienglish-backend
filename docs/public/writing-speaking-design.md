---
feature: writing-speaking
type: design
status: approved        # design chốt, chưa implement
last-updated: 2026-06-02
author: Backend team
depends-on: [attempts, questions, media-upload, teacher-grading]
---

# Writing & Speaking Question Types — Thiết kế & Core feature

> **Scope**: thêm 2 loại câu hỏi mới (`writing`, `speaking`) + section type tương
> ứng, và cách student trả lời (gõ text / ghi âm-ghi hình → upload). **Dừng ở submit.**
> Việc teacher chấm + comment + email E5 là **tính năng riêng** → xem
> `docs/teacher-grading/`.
>
> Bổ sung cho [`MAICHIENGLISH_BACKEND_PLAN.md`](../../MAICHIENGLISH_BACKEND_PLAN.md)
> §3 (schema) + §4 (API), [`attempt-lifecycle design §4`](../attempt-lifecycle/attempt-lifecycle-design.md#4-state-machine)
> (state machine), và [`MEDIA_UPLOAD.md`](../../MEDIA_UPLOAD.md) (signed-URL upload,
> reuse cho student speaking audio/video).
>
> Tài liệu cùng feature: [Frontend notes](writing-speaking-frontend.md) ·
> [Test cases](writing-speaking-testcases.md). Chi tiết API xem **Swagger**.

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [Current state — what's missing](#3-current-state--whats-missing)
4. [Writing question type](#4-writing-question-type)
5. [Speaking question type](#5-speaking-question-type)
6. [Section type changes](#6-section-type-changes)
7. [Submit behavior changes](#7-submit-behavior-changes)
8. [Storage — speaking answer audio/video](#8-storage--speaking-answer-audiovideo)
9. [Migration plan (phần writing-speaking)](#9-migration-plan-phần-writing-speaking)
10. [API changes summary](#10-api-changes-summary)
11. [Future v2 roadmap](#11-future-v2-roadmap)
12. [Appendix: files to change](#12-appendix-files-to-change)

---

## 1. Overview

Hai loại câu hỏi mới mở rộng exam model:

| Type | Student làm gì | Chấm thế nào |
|---|---|---|
| `writing` | Gõ text vào textarea | Teacher đọc + cho điểm tay (→ feature teacher-grading) |
| `speaking` | **Upload file audio/video có sẵn** lên storage | Teacher nghe/xem + cho điểm tay (→ feature teacher-grading) |

> **v1 = upload-only.** Học sinh tải lên file audio/video có sẵn — **chưa quay/ghi
> trực tiếp trên trình duyệt**. Quay trực tiếp là tính năng **v2 duy nhất** ([§11](#11-future-v2-roadmap)).

Đặc điểm chung:
- Không có `correct_answer` / `correct_index` trong `question_data`.
- Auto-grade **bỏ qua hoàn toàn** lúc submit (`is_correct=NULL`, `points_earned=0` cho tới khi teacher chấm).
- Cờ attempt-level `is_fully_graded` phân biệt "chỉ auto-graded" với "đã hoàn tất". Submit set `false` nếu exam có bất kỳ câu writing/speaking. **Việc lật cờ về `true` + lock thuộc feature teacher-grading.**

```
[ during attempt ]              [ at submit ]                       [ post-submit ]
─────────────────               ──────────────                       ──────────────
 gõ text       ─┐               POST /submit                        → feature teacher-grading
                ├──► PATCH /answers  → auto-grade MC/fill/match        (chấm, comment, badge,
 upload file A/V┘     (không endpoint → skip writing/speaking           visibility, lock…)
                    mới)             → is_fully_graded = false
                                       (nếu exam có writing/speaking)
```

Doc này phủ tới **at submit**. Vòng đời post-submit nằm ở `docs/teacher-grading/`.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Ràng buộc số từ writing | **Không enforce min/max words ở server** | Server reject nếu ngoài min/max | KET/PET word count là *guidance*, không phải pass/fail; marker thật chấp nhận lệch ±20%. Hard limit sẽ reject bài hợp lệ. FE có thể cảnh báo. |
| 2 | Hình dạng `student_answer` writing | **Object wrapper `{text}`** | Bare string | Để sau thêm `wordCount`, `autoSaveTimestamp`… mà không vỡ schema. |
| 3 | Cách student nộp speaking | **Upload file có sẵn (upload-only)** | Quay/ghi trực tiếp trên browser | Thu hẹp scope v1; app chỉ dùng trên laptop/PC nên upload file là đủ. Quay trực tiếp → v2 (tính năng v2 duy nhất). |
| 4 | Loại media speaking | **Mỗi câu nhận *cả* audio lẫn video**, FE chỉ định | Ép cứng audio-only/video-only per câu | v1 đơn giản; admin hướng dẫn qua `prompt`. |
| 5 | `mediaType` do ai quyết | **FE gửi `"audio"`/`"video"` (suy từ file đã chọn); BE lưu nguyên** | BE đọc metadata file để suy | FE biết file gì; BE không cần đọc metadata. |
| 6 | Bucket lưu recording | **Bucket riêng `student_recordings`** | Dùng chung bucket `audio` của admin | Khác writer (student vs admin), khác nội dung (mixed A/V), khác lifecycle (orphan nhiều do re-upload), ranh giới sạch cho migration sau. |
| 7 | Size cap recording | **50 MB (Supabase Free tier)** | 100 MB ngay | Free plan cap 50 MB/file; đủ cho 1–2 phút KET speaking. Lên paid plan thì bump 100 MB (sync `SIZE_LIMITS` + Dashboard). |
| 8 | MIME whitelist speaking | **audio/webm,mp4,mpeg,wav + video/webm,mp4** (KHÔNG nhận `video/quicktime` .mov) | Nhận thêm .mov cho iPhone | App **chỉ dùng trên laptop/PC** (không hỗ trợ điện thoại) nên file .mov gốc iPhone gần như không xảy ra; thêm nữa .mov/HEVC phát lại không ổn định trên Chrome/Firefox. |
| 9 | `is_fully_graded` default | **`true`** | `false` | Mọi attempt cũ là auto-graded → fully graded theo định nghĩa. Chỉ attempt trên exam có writing/speaking mới lật `false` lúc submit. |
| 10 | Validate shape answer lúc save | **Không validate lúc save** (giữ quy ước ATTEMPT_LIFECYCLE §4.3) | Validate chặt khi save | Cho phép lưu nháp linh hoạt (text rỗng, thiếu mediaType…). Ràng buộc dồn về lúc grade. |

---

## 3. Current state — what's missing

| Layer | Hiện tại | Gap (feature này lấp) |
|---|---|---|
| Question types | `multiple_choice`, `fill_blank`, `matching` | Không có loại free-text / ghi âm |
| Section type CHECK | `multiple_choice`, `multiple_choice_shared`, `fill_blank`, `matching` | Không có `writing` / `speaking` |
| Attempt state | `in_progress` / `submitted` / `abandoned` | Không diễn đạt được "đã nộp nhưng chờ chấm" |
| Storage | Chỉ admin upload (`audio`, `images`) | Không có đường student upload audio/video |

> Gap về grading (chưa có endpoint chấm tay) thuộc feature **teacher-grading**.

---

## 4. Writing question type

### 4.1 `question_data` shape

```jsonc
{
  "prompt": "Write an email to your friend about your last holiday.",
  "minWords": 25,           // optional, FE hiển thị như gợi ý; BE KHÔNG enforce
  "maxWords": 35,           // optional, FE hiển thị như gợi ý; BE KHÔNG enforce
  "exampleAnswer": "..."    // optional, admin preview; KHÔNG BAO GIỜ gửi cho student
}
```

Chỉ `prompt` bắt buộc. (Lý do không enforce min/max: xem [§2](#2-design-decisions--trade-offs) #1.)

### 4.2 `student_answer` shape

```jsonc
{ "text": "Hi Anna, I had a great holiday in Da Nang last week..." }
```

Object wrapper (không phải bare string) để mở rộng sau ([§2](#2-design-decisions--trade-offs) #2).

### 4.3 Strip rules

Writing không có `correct_index`/`correct_answers` → `strip_correct` là no-op.
`exampleAnswer` **luôn bị strip** trước khi trả cho student (chỉ admin/teacher thấy).

### 4.4 Grading

- Auto-grade: **skip**. Lúc submit, UPSERT answer row `is_correct=NULL, points_earned=0`. `question.points` vẫn cộng vào `total_points` nhưng answer đóng góp `0` vào `score` cho tới khi chấm.
- Manual grade: thuộc feature teacher-grading.

### 4.5 Validation lúc save

- `student_answer.text` phải là string. Empty string OK (lưu nháp).
- Không giới hạn độ dài lúc save lẫn submit (lý do [§4.1](#41-question_data-shape) / [§2](#2-design-decisions--trade-offs) #1).

---

## 5. Speaking question type

### 5.1 `question_data` shape

```jsonc
{
  "prompt": "Describe a person in your family.",
  "promptAudioUrl": null,       // optional, audio examiner đọc đề (admin upload vào bucket `audio`)
  "exampleAnswerAudioUrl": null // optional, KHÔNG gửi cho student; admin reference
}
```

Chỉ `prompt` bắt buộc. Ý nghĩa các field optional (BE lưu nguyên, FE diễn giải) — xem
bảng data semantics ở [Frontend notes](writing-speaking-frontend.md).

> **Lưu ý:** `maxDurationSeconds` và `prepTimeSeconds` (đếm ngược/tự dừng) chỉ có
> nghĩa khi quay trực tiếp → **bỏ khỏi v1**, dời sang v2 cùng tính năng quay
> ([§11](#11-future-v2-roadmap)). Đề speaking v1 chỉ cần `prompt` (+ optional `promptAudioUrl`).

### 5.2 `student_answer` shape

```jsonc
{
  "recordingUrl": "https://xxx.supabase.co/storage/v1/object/public/student_recordings/a1b2c3d4.webm",
  "mediaType": "audio",           // "audio" | "video" — FE chỉ định theo cái đã ghi
  "durationSeconds": 43           // optional, FE có thể gửi; BE không validate
}
```

- `recordingUrl` — public URL sau khi upload xong (flow [§8](#8-storage--speaking-answer-audiovideo)).
- `mediaType` — `"audio"`/`"video"`; FE suy từ loại file student upload; quyết định downstream render `<audio>` vs `<video>`. BE lưu nguyên.
- `durationSeconds` — optional; FE có thể bỏ qua (upload-only không có sẵn lúc ghi). BE không validate.
- v1 KHÔNG ràng buộc per-question — câu speaking nhận cả hai loại.

### 5.3 Strip rules

Giống writing — không field answer để strip. `exampleAnswerAudioUrl` admin-only (luôn strip trước khi trả student).

### 5.4 Grading

Giống writing — skip lúc auto-grade, set lúc manual grade (teacher-grading).

### 5.5 Re-upload trước submit

Student có thể upload lại bao nhiêu lần tùy ý trước submit. Mỗi lần = một upload =
một URL mới. Có thể đổi audio↔video giữa các lần. Save ghi đè `recordingUrl` +
`mediaType` mới nhất. File cũ thành orphan trong storage (theo orphan-cleanup của
[`MEDIA_UPLOAD.md §10`](../../MEDIA_UPLOAD.md) — thủ công, không enforce v1).

### 5.6 No transcription

Server KHÔNG transcribe (audio/video). Teacher nghe/xem trực tiếp.

---

## 6. Section type changes

`sections.type` là **rendering hint** (đã vậy theo
[`migrations/0005_section_type.sql`](../../migrations/0005_section_type.sql) và
[`migrations/0008_section_type_mc_shared.sql`](../../migrations/0008_section_type_mc_shared.sql)).

### 6.1 New values

Thêm `'writing'` và `'speaking'` vào CHECK constraint (SQL đầy đủ ở [§9](#9-migration-plan-phần-writing-speaking)).

### 6.2 Mixing types within a section

`section.type` là **hint**, không phải hard contract. Section `type='writing'` về kỹ
thuật có thể chứa câu `multiple_choice` nếu admin wire vậy — server không validate.
Admin được kỳ vọng giữ type nhất quán trong section.

### 6.3 Typical KET section structure

| Section | Type | Questions |
|---|---|---|
| Part 6 | `fill_blank` | 5 fill-blank words |
| Part 7 | `writing` | 1 short-message prompt (~25 words) |
| Part 9 | `writing` | 1 longer prompt (~35 words) |
| Speaking Part 1 | `speaking` | 1 intro Q&A |
| Speaking Part 2 | `speaking` | 1 picture description |

Mỗi section writing/speaking thường có 1 câu, nhưng schema cho phép N.

---

## 7. Submit behavior changes

### 7.1 New attempt-level flag

```sql
ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS is_fully_graded boolean NOT NULL DEFAULT true;
```

**Default `true`** (lý do [§2](#2-design-decisions--trade-offs) #8). Chỉ attempt trên exam
có ≥1 câu writing/speaking mới lật `false` lúc submit.

### 7.2 Submit algorithm (đã đổi)

```
# trong attempt_service.submit_attempt — flow sửa đổi:
1. Existing checks (owner, not submitted, not abandoned) — unchanged.
2. Merge body + saved answers — unchanged.
3. Grade MỖI câu:
   - Nếu question_type ∈ ('writing','speaking'): UPSERT answer is_correct=NULL, points_earned=0
   - Else: grade_question() như cũ → UPSERT is_correct, points_earned
4. score = sum(points_earned trên TẤT CẢ answers, kể cả 0 cho writing/speaking)
5. total_points = sum(question.points trên TẤT CẢ câu trong exam)
6. percentage = score / total_points * 100
7. has_manual = exists(question_type ∈ ('writing','speaking') trong exam này)
8. UPDATE attempts SET score, total_points, percentage, time_spent_seconds,
     submitted_at = now(), is_fully_graded = NOT has_manual
```

`score`/`percentage` lúc này phản ánh **chỉ phần auto-gradable**. Sau khi teacher chấm, cả hai được recompute (feature teacher-grading).

### 7.3 Response shape

`POST /api/attempts/{id}/submit` vẫn trả `{score, totalPoints, percentage, submittedAt}`,
nay thêm `isFullyGraded` để FE biết hiện "Đã chấm" hay "Đang chấm phần writing/speaking".

```jsonc
{ "status": 200, "data": {
  "score": 5.5, "totalPoints": 10, "percentage": 55.0,
  "submittedAt": "...", "isFullyGraded": false } }   // isFullyGraded: NEW
```

### 7.4 No change to abandon

Abandon xảy ra từ `in_progress` — trước khi submit chạy — nên `is_fully_graded` chưa
bao giờ bị lật `false`, giữ default `true`. Không chấm gì cho abandoned (score=0 theo
abandon rules); email E5 KHÔNG fire khi abandon.

---

## 8. Storage — speaking answer audio/video

### 8.1 New bucket

```
Bucket: student_recordings
Public: yes (giống bucket `audio`)
MIME whitelist:
  - Audio: audio/webm, audio/mp4, audio/mpeg, audio/wav
  - Video: video/webm, video/mp4
Size limit: 50 MB (Supabase Free tier; lên 100 MB khi paid plan)
RLS read: anon SELECT allowed (URL UUID, không enumerable)
```

Lý do bucket riêng + cap 50 MB: xem [§2](#2-design-decisions--trade-offs) #5, #6.

### 8.2 Upload endpoint (mới, student-facing)

```
POST /api/attempts/{attempt_id}/speaking-upload
Authorization: Bearer <student-token>
```

Tên giữ "speaking" vì là scope theo loại câu, không phải định dạng file. Audio + video
đều đi qua đây.

Request (audio): `{questionId, filename, contentType: "audio/webm", fileSizeBytes}`.
Request (video): `{questionId, filename, contentType: "video/webm", fileSizeBytes}`.

Response 200: `{uploadUrl, publicUrl, token, path, bucket: "student_recordings"}`.
FE PUT binary trực tiếp lên `uploadUrl`, lưu `publicUrl` (làm `recordingUrl`) +
`mediaType` (suy từ `contentType`) vào `student_answer` qua `PATCH /answers`.

### 8.3 Validation

Giống `POST /api/admin/upload` ([MEDIA_UPLOAD.md §5.1](../../MEDIA_UPLOAD.md)), thêm:

| Check | Failure |
|---|---|
| Caller sở hữu `attempt_id` | 403 `Not the owner of this attempt` |
| Attempt đang in_progress (chưa submit, chưa abandon) | 400 `Attempt is not active` |
| `questionId` thuộc exam của attempt | 404 |
| `question_type` của câu là `'speaking'` | 400 `Question is not a speaking question` |
| `contentType` trong whitelist `student_recordings` | 422 |
| Size ≤ 50 MB (Free tier; 100 MB khi paid) | 422 |

Extension suy từ `contentType` qua `EXT_FOR_MIME` (KHÔNG từ filename) — như MEDIA_UPLOAD §3.2.
Thêm `video/webm → .webm`, `video/mp4 → .mp4` (audio MIME đã có).

### 8.4 Adapter reuse

Cùng `SupabaseStorageAdapter` của MEDIA_UPLOAD — chỉ khác tên bucket. Không code path
mới; chỉ thêm entry whitelist `ALLOWED_TYPES['student_recordings']` + 2 entry
`EXT_FOR_MIME`.

### 8.5 Orphans

Re-record / đổi media type sinh orphan trong `student_recordings`. Cùng orphan policy
MEDIA_UPLOAD §10 — không có v1; cleanup thủ công khi storage đầy.

---

## 9. Migration plan (phần writing-speaking)

> Lưu ý: migration thực tế `0012_writing_speaking_question_types.sql` gộp **cả** phần
> teacher-grading (`writing_comments`, `answers.speaking_comment*`). Phần đó tài liệu
> tại `docs/teacher-grading/`. Dưới đây chỉ phần thuộc writing-speaking.

```sql
-- 1. Extend sections.type CHECK
ALTER TABLE public.sections DROP CONSTRAINT IF EXISTS sections_type_check;
ALTER TABLE public.sections ADD CONSTRAINT sections_type_check CHECK (
  type IS NULL OR type IN (
    'multiple_choice', 'multiple_choice_shared', 'fill_blank', 'matching',
    'writing', 'speaking'));

-- 2. Extend questions.question_type CHECK
ALTER TABLE public.questions DROP CONSTRAINT IF EXISTS questions_type_check;
ALTER TABLE public.questions ADD CONSTRAINT questions_type_check CHECK (
  question_type IN ('multiple_choice', 'fill_blank', 'matching', 'writing', 'speaking'));

-- 3. Add is_fully_graded flag (default true — existing attempts auto-graded only)
ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS is_fully_graded boolean NOT NULL DEFAULT true;
```

`schema.sql` update lockstep để `scripts/init_schema.py --drop` ra greenfield khớp.

### 9.1 Supabase Storage manual setup

Dashboard → Storage → New bucket:

| Field | Value |
|---|---|
| Name | `student_recordings` |
| Public | ✅ |
| Restrict file uploads | ✅ |
| Allowed MIME types | `audio/webm, audio/mp4, audio/mpeg, audio/wav, video/webm, video/mp4` |
| File size limit | `50 MB` (Free tier; 100 MB khi paid, sync `SIZE_LIMITS["student_recordings"]`) |

RLS read policy (SQL Editor, giống bucket `audio` per [`MEDIA_UPLOAD.md §9.2 Step C`](../../MEDIA_UPLOAD.md)):

```sql
CREATE POLICY "Public read access for student_recordings"
  ON storage.objects FOR SELECT USING (bucket_id = 'student_recordings');
```

---

## 10. API changes summary

### 10.1 New endpoint

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/api/attempts/{id}/speaking-upload` | Signed URL cho student speaking recording (audio/video) | Student (owner) |

> Các endpoint grade/comment thuộc feature teacher-grading.

### 10.2 Modified endpoints

| Endpoint | Change |
|---|---|
| `POST /api/attempts/{id}/submit` | Response thêm `isFullyGraded: bool`; submit skip auto-grade cho writing/speaking; set `is_fully_graded` theo cấu trúc exam |
| `GET /api/attempts/{id}` | `attempt` view thêm `isFullyGraded: bool`. (Comment fields trên answer thuộc teacher-grading.) |
| `GET /api/attempts/history` | Mỗi item thêm `isFullyGraded: bool` để FE hiện badge "Đang chấm" |
| `POST /api/exams` / `PUT /api/exams/{id}` (admin) | `question_type` + `section.type` nhận giá trị mới; validate per-type (writing/speaking đều cần `prompt`) |

### 10.3 Schemas

- `AttemptView.isFullyGraded: bool = True` (NEW)
- `AttemptHistoryItem.isFullyGraded: bool = True` (NEW)
- `AttemptSubmitResponseData.isFullyGraded: bool` (NEW)

```python
class WritingQuestionData(BaseModel):
    prompt: str = Field(..., min_length=1)
    minWords: Optional[int] = Field(default=None, ge=1)
    maxWords: Optional[int] = Field(default=None, ge=1)
    exampleAnswer: Optional[str] = None

class SpeakingQuestionData(BaseModel):
    prompt: str = Field(..., min_length=1)
    promptAudioUrl: Optional[str] = None
    exampleAnswerAudioUrl: Optional[str] = None
    # maxDurationSeconds / prepTimeSeconds → v2 (đi cùng tính năng quay trực tiếp)
```

Save answer KHÔNG validate shape (giữ ATTEMPT_LIFECYCLE §4.3 / test S9).

---

## 11. Future v2 roadmap

> **v2 chỉ có một tính năng** cho writing-speaking. (Re-grading/audio-feedback của teacher thuộc roadmap teacher-grading.)

- **Quay/ghi trực tiếp trên trình duyệt** — thay vì upload file có sẵn, student ghi audio/video ngay trong browser bằng `MediaRecorder` rồi upload. Kéo theo: feature-detect MIME (`isTypeSupported` — Safari không hỗ trợ webm), chờ event `stop` trước khi gom Blob, `prepTimeSeconds` (đếm ngược chuẩn bị), `maxDurationSeconds` (tự dừng). Endpoint upload + bucket + lưu trữ tái dùng nguyên từ v1 — chỉ thêm phần ghi ở FE.

---

## 12. Appendix: files to change

### 12.1 New files

| File | Purpose |
|------|---------|
| `migrations/0012_writing_speaking_question_types.sql` | Section + question type CHECK; `attempts.is_fully_graded`; (phần comment thuộc teacher-grading nhưng cùng migration) |

### 12.2 Modified files (phần writing-speaking)

| File | Change |
|------|---------|
| [api/questions/schemas.py](../../api/questions/schemas.py) | Thêm `WritingQuestionData`, `SpeakingQuestionData`; per-type validator |
| [services/question_service.py](../../services/question_service.py) | Per-type validation cho writing/speaking |
| [utils/grading_utils.py](../../utils/grading_utils.py) | `grade_question` trả `(None, 0)` cho writing/speaking; `strip_correct` strip `exampleAnswer`/`exampleAnswerAudioUrl` |
| [services/attempt_service.py](../../services/attempt_service.py) | `submit_attempt`: skip auto-grade, compute `is_fully_graded` |
| [api/attempts/schemas.py](../../api/attempts/schemas.py) | Thêm `isFullyGraded` vào `AttemptView`, `AttemptHistoryItem`, `AttemptSubmitResponseData` |
| [api/attempts/routes.py](../../api/attempts/routes.py) | `_attempt_to_view` truyền `isFullyGraded`; thêm `POST /attempts/{id}/speaking-upload` |
| [services/storage_service.py](../../services/storage_service.py) | Thêm `student_recordings` vào `ALLOWED_TYPES` + `SIZE_LIMITS`; thêm `video/webm`, `video/mp4` vào `EXT_FOR_MIME` |

### 12.3 No new env vars

Reuse Supabase Storage creds (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`). `student_recordings` chỉ là tên bucket mới cho cùng adapter.
