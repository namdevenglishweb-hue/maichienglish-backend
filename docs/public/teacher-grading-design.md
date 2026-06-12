---
feature: teacher-grading
type: design
status: partial         # core + class-scoping đã wire (route layer); chỉ còn E5 email chờ email feature
last-updated: 2026-06-03
author: Backend team
depends-on: [writing-speaking, attempts, email, class-management]
---

# Teacher Grading & Comments — Thiết kế & Core feature

> **Scope**: phase *post-submit* của bài có câu writing/speaking — giáo viên chấm
> điểm tay, hệ thống comment (writing range-based + speaking overall), lật cờ
> `is_fully_graded`, khóa, và email báo học sinh.
>
> Phase *trước submit* (loại câu, student trả lời, upload, set cờ `false`) là **feature
> riêng** → xem `docs/writing-speaking/`.
>
> Bổ sung cho [`attempt-lifecycle design`](../attempt-lifecycle/attempt-lifecycle-design.md) (state machine) và
> dùng đường email E5 thiết kế trong `docs/email/`.
>
> Tài liệu cùng feature: [Frontend notes](teacher-grading-frontend.md) ·
> [Test cases](teacher-grading-testcases.md). Chi tiết API xem **Swagger**.

---

## 🛠️ Trạng thái triển khai (đọc trước khi code)

Đây là **amend, KHÔNG viết mới**:

| Phần | Trạng thái | Ghi chú |
|------|-----------|---------|
| Grade endpoint (`POST /api/teacher/attempts/{id}/grade`) | ✅ **Có code + class-scoping** ([api/teacher/routes.py](../../api/teacher/routes.py) `_authorize_attempt_scope`) | Check `teacher_shares_class_with(teacher, attempt.user_id)` ở **route layer**, admin bypass → 403 |
| Comment endpoints (writing range + speaking) | ✅ **Có code + class-scoping** | Cùng `_authorize_attempt_scope` ở đầu cả 5 endpoint |
| Attempt detail visibility cho teacher | ✅ **Đã siết class-scoped** ([api/attempts/routes.py](../../api/attempts/routes.py) `get_attempt_detail`) | Teacher viewer → `teacher_shares_class_with`; owner/admin/parent giữ nguyên |
| RBAC helpers `teacher_shares_class_with` / `teacher_teaches_class` | ✅ **Đã có** ([class_service.py](../../services/class_service.py)) | Được gọi từ route layer |
| Strip đáp án (in_progress ẩn / submitted+abandoned hiện) | ✅ Code đã đúng (option A) | Không đụng |
| Email E5 "bài đã chấm" | ⏳ Chỉ **log** (email feature chưa build) | Wire khi email feature ra ([§5](#5-email-notification-on-full-grading-e5)) |

→ Class-scoping đã hoàn tất (phase 2, route layer). Còn lại duy nhất E5 email chờ email feature.

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [Manual grading endpoint](#3-manual-grading-endpoint)
4. [Teacher comments — writing (range) & speaking (overall)](#4-teacher-comments--writing-range--speaking-overall)
5. [Email notification on full grading (E5)](#5-email-notification-on-full-grading-e5)
6. [RBAC & visibility](#6-rbac--visibility)
7. [Lock — grades & comments freeze](#7-lock--grades--comments-freeze)
8. [Migration plan (phần teacher-grading)](#8-migration-plan-phần-teacher-grading)
9. [API changes summary](#9-api-changes-summary)
10. [Future v2 roadmap](#10-future-v2-roadmap)
11. [Appendix: files to change](#11-appendix-files-to-change)

---

## 1. Overview

Sau khi student nộp bài có câu writing/speaking, auto-grader đã skip những câu đó
(`is_correct=NULL, points_earned=0`, cờ `is_fully_graded=false`). Teacher (hoặc admin):

- **Cho điểm** từng câu writing/speaking (con số `pointsEarned`).
- (Tùy chọn) thêm **nhận xét writing theo đoạn bôi đen** (range-based, nhiều/đáp án).
- (Tùy chọn) thêm **nhận xét speaking tổng thể** (1 đoạn text/đáp án).

Khi **tất cả** câu writing/speaking đã được cho điểm → cờ `is_fully_graded` lật
`false → true`, điểm chốt lại, **email E5 gửi học sinh**, và grade + comment **bị khóa**.

```
[ pending grade ]                              [ fully graded ]
─────────────────                               ────────────────
Teacher cho điểm từng câu (chấm dần được)        is_fully_graded: true
Teacher (optional) thêm nhận xét                 điểm chốt
Student/parent thấy nhận xét NGAY (live)         email "Bài đã chấm" → student
cờ vẫn false đến khi câu cuối được chấm           grade + comment khóa
```

**Điểm mấu chốt — điểm vs nhận xét là 2 việc độc lập:**
- *Điểm* (số) quyết định trạng thái fully-graded. Writing và speaking **đều** cho điểm bằng con số.
- *Nhận xét* là tùy chọn, không ảnh hưởng cờ. Chỉ **cách** nhận xét khác nhau: writing = nhiều đoạn bôi đen; speaking = 1 đoạn text duy nhất.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Độ mịn chấm | **Per-question, chấm dần (partial) được** | Batch-all một lần | Teacher chấm linh hoạt, quay lại sau. Mỗi grade write atomic. |
| 2 | Lật `is_fully_graded` | **Một chiều `false→true`, rồi khóa** | Cho lật qua lại | Chống score-drift. Re-grade cần audit chặt → v2. |
| 3 | Sửa điểm sau fully-graded | **Reject (400)** | Cho sửa tự do | Bảo vệ điểm đã chốt. |
| 4 | Encode nhận xét writing | **Char offset half-open `[start,end)`** | Word offset / DOM selector | Text bất biến sau submit → offset không trôi; BE validate bằng so sánh int; FE lấy offset từ `getSelection()` native; bền khi đổi format hiển thị. |
| 5 | Chống chồng range | **App-layer + row lock** | DB UNIQUE constraint | Partial-overlap không biểu diễn được bằng UNIQUE. Lock answer row để serialize. |
| 6 | Lưu nhận xét speaking | **Inline columns trên `answers`** | Bảng `speaking_comments` riêng | Tối đa 1 comment/đáp án → không cần list; UPSERT đơn giản; không tốn JOIN. |
| 7 | Visibility nhận xét | **Hiện ngay khi teacher thêm** (trước fully-graded) | Đợi chấm xong mới hiện | Student xem feedback từng phần sớm. |
| 8 | Khóa nhận xét | **Khóa cùng lúc với grade khi fully-graded** | Comment độc lập, không khóa | Đồng nhất "bài đã chốt thì khóa hết". |
| 9 | Ai chấm/nhận xét | **Teacher hoặc admin; bất kỳ teacher** (không chỉ người tạo comment) | Chỉ author sửa comment | Đội teaching chia tải chấm. `created_by` giữ nguyên, `updated_at` bump. |
| 10 | `is_correct` cho câu đã chấm tay | **Set `true` = quy ước "đã chấm"** | Để NULL | Phân biệt "đã chấm" với "chưa chấm". Partial-correct tinh vi → v2. |
| 11 | Người nhận E5 | **Chỉ student** (chủ attempt) | Cả parent/teacher | Parent đã nhận E3 lúc submit; teacher là người chấm. |

---

## 3. Manual grading endpoint

**Scope cố ý tối thiểu**: v1 chấm per-question chỉ với điểm số. Overall comment cả
bài, audio feedback → [§10](#10-future-v2-roadmap).

```
POST /api/teacher/attempts/{attempt_id}/grade
Authorization: Bearer <teacher-or-admin-token>
```

Mount dưới `/api/teacher/` với `dependencies=[Depends(require_teacher_or_admin)]`.

Request: `{ "grades": [ {questionId, pointsEarned}, ... ] }` — `pointsEarned` là
**int**, `0 ≤ pointsEarned ≤ question.points`.
Response: `{graded, attemptScore, attemptTotalPoints, attemptPercentage, isFullyGraded}`.

### 3.1 Validation

| Check | Failure |
|---|---|
| Teacher dạy lớp của HS (`teacher_shares_class_with`); admin bypass | **403** (per [§6](#6-rbac--visibility)) |
| `grades` non-empty | 422 |
| Mỗi `questionId` thuộc exam của attempt | 404 (cả batch rollback) |
| `question_type` là `'writing'`/`'speaking'` | 400 `not manually-graded` |
| `pointsEarned` int, `0 ≤ x ≤ question.points` | 422 |
| Attempt đã submitted (không in_progress/abandoned) | 400 |
| `is_fully_graded` đang `false` (còn mở để chấm) | 400 `already fully graded` |

### 3.2 Hành vi

1. `SELECT ... FOR UPDATE` attempt row.
2. Validate state + mọi questionId thuộc exam + đúng manual type + range điểm.
3. UPSERT answer rows: set `points_earned`, `is_correct=true` (quy ước "đã chấm" — [§2](#2-design-decisions--trade-offs) #10).
4. Recompute `score`/`total_points`/`percentage` từ TẤT CẢ answer.
5. Đếm câu manual chưa chấm (`is_correct IS NULL` hoặc answer vắng). Nếu = 0 → set `is_fully_graded=true` + schedule E5 email.

### 3.3 Concurrency

`SELECT ... FOR UPDATE` serialize 2 teacher chấm cùng attempt — một cái chờ. UPSERT
nên idempotent; lật cờ race-safe nhờ row lock.

### 3.4 Partial grading

Chấp nhận subset non-empty bất kỳ. `is_fully_graded` chỉ lật khi **mọi** câu
writing/speaking trong exam đã chấm.

### 3.5 v1 KHÔNG hỗ trợ trên endpoint chấm

(→ [§10](#10-future-v2-roadmap).) Overall comment cả bài; audio feedback; re-grade sau
khóa; partial-credit tinh vi cho câu auto-graded. *Lưu ý: nhận xét per-answer CÓ ở v1
nhưng nằm trên endpoint riêng — xem [§4](#4-teacher-comments--writing-range--speaking-overall).*

---

## 4. Teacher comments — writing (range) & speaking (overall)

> Thiết kế up-front ở v1 để tránh retrofit sau. Hai pattern UX:
> - **Writing**: bôi đen một đoạn text → gắn comment (kiểu Word). Nhiều range/đáp án; không chồng nhau; tối đa 1 comment/range.
> - **Speaking**: 1 comment free-form cho cả bài (timestamp ghi tay trong text, server không parse).

### 4.1 Range encoding (writing)

Char offset, 0-indexed, half-open `[rangeStart, rangeEnd)`:
- `rangeStart` inclusive; `rangeEnd` exclusive (giống JS `substring(start,end)`).
- Empty range (`end==start`) bị reject.
- Bound: `0 ≤ rangeStart < rangeEnd ≤ length(text)`.

Overlap của `(s1,e1)` và `(s2,e2)`:

| Điều kiện | Kết quả |
|---|---|
| `s1 < e2 AND s2 < e1` | **Overlap** — comment thứ 2 reject |
| `e1 == s2` (chạm nhưng không đè) | Allowed |
| disjoint | Allowed |

Lý do char offset (không phải word/DOM selector): xem [§2](#2-design-decisions--trade-offs) #4.

### 4.2 Schema

```sql
CREATE TABLE IF NOT EXISTS public.writing_comments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id     uuid NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
  range_start   int  NOT NULL CHECK (range_start >= 0),
  range_end     int  NOT NULL CHECK (range_end > range_start),
  quoted_text   text NOT NULL,
  comment_text  text NOT NULL CHECK (length(comment_text) > 0),
  created_by    uuid NOT NULL REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS writing_comments_answer_id_idx
  ON public.writing_comments (answer_id, range_start);
```

- Không UNIQUE `(answer_id, range_start, range_end)` — overlap enforce ở app-layer ([§2](#2-design-decisions--trade-offs) #5).
- `created_by` ON DELETE SET NULL — giữ comment nếu staff account bị xóa.
- `quoted_text` là snapshot `text[start:end]` lúc tạo (để display + audit).

```sql
-- speaking comment — inline columns (tối đa 1/đáp án)
ALTER TABLE public.answers
  ADD COLUMN IF NOT EXISTS speaking_comment      text,
  ADD COLUMN IF NOT EXISTS speaking_comment_by   uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS speaking_comment_at   timestamptz;
```

### 4.3 Endpoints

Tất cả mount dưới `/api/teacher/` (`require_teacher_or_admin`).

**Writing comments:**
```
POST   /api/teacher/attempts/{id}/answers/{aid}/writing-comments
PUT    /api/teacher/attempts/{id}/answers/{aid}/writing-comments/{cid}
DELETE /api/teacher/attempts/{id}/answers/{aid}/writing-comments/{cid}
```
- **POST** thêm range comment mới → 201. Validate (tóm tắt): role teacher/admin; attempt submitted, không abandoned, chưa fully-graded; answer là `writing`; range bound hợp lệ; `quotedText == text[start:end]` (strict); `commentText` non-empty; **không overlap** comment hiện có.
- **PUT** chỉ sửa `commentText` (range bất biến — đổi range thì DELETE rồi POST). Bất kỳ teacher/admin; `created_by` giữ nguyên, `updated_at` bump.
- **DELETE** → 204 nếu xóa được; 404 nếu đã xóa/không tồn tại. FE handle 404 im lặng.

**Speaking comment:**
```
PUT    /api/teacher/attempts/{id}/answers/{aid}/speaking-comment
DELETE /api/teacher/attempts/{id}/answers/{aid}/speaking-comment
```
- **PUT** = UPSERT 1 comment (ghi `speaking_comment`, `_by`, `_at=now()`). Validate: answer là `speaking`; attempt submitted/không abandoned/chưa fully-graded; `commentText` non-empty. Không có `updatedAt` (PUT ghi đè toàn bộ, không lưu edit history).
- **DELETE** set cả 3 cột về NULL → 204; 404 nếu cả 3 đã NULL.

### 4.4 Concurrency (writing comments)

`SELECT id FROM answers WHERE id=$1 FOR UPDATE` serialize insert trên cùng answer →
2 teacher comment đồng thời: một cái chờ; cả hai thành công nếu range không đè; một
fail 400 nếu đè. Khác answer → không tranh chấp.

### 4.5 Response trong `GET /api/attempts/{id}`

- Mỗi writing answer mang `writingComments: [...]` (rỗng `[]` nếu chưa có).
- Mỗi speaking answer mang `speakingComment: {...}` hoặc `null`.
- Câu MC/fill/matching: 2 field này **vắng** — FE key theo questionType.
- Trạng thái: pre-comment → `writingComments:[]`, `speakingComment:null`; post-comment pre-grade → comment hiện, `pointsEarned:0`, `isCorrect:null`; post-grade → đủ cả.

---

## 5. Email notification on full grading (E5)

> ⚠️ **Trạng thái hiện tại: E5 CHƯA gửi được.** Email infrastructure (`docs/email/`)
> **chưa được implement trong code** — chưa có `services/email_service.py`. Vì vậy ở
> code hiện tại ([api/teacher/routes.py](../../api/teacher/routes.py)), khi cờ lật
> `false→true`, route **chỉ log** "E5 student-graded email NOT YET SENT (email feature
> not built)" + fetch sẵn `student_email`. **Không có email nào thực sự rời hệ thống.**
> Học sinh hiện chỉ biết bài đã chấm khi tự mở `GET /attempts/{id}`.

Thiết kế (sẽ wire khi email feature ra đời):
- **Trigger**: trong `POST /grade`, ngay sau update `is_fully_graded=true` (chỉ ở lần lật `false→true`).
- **Template**: nằm ở email feature (`student_graded`), không duplicate ở đây. Khi build email: chuyển E5 từ roadmap sang v1; thêm `services/email_templates/student_graded.py`; thay log bằng `schedule_email(...)` (chỗ TODO đã đánh dấu sẵn trong route).
- **Recipient**: `student.email` (chủ attempt). Không parent (đã nhận E3 lúc submit), không teacher.
- **Nếu email fail**: background task nuốt lỗi. Email là nudge, không phải kênh chính thức.

---

## 6. RBAC & visibility

> ⚠️ **Scoped theo lớp (class-management).** Teacher chỉ thao tác được trên bài của
> **học sinh thuộc lớp mình dạy** — authorize qua `teacher_shares_class_with(teacher, student)`
> (xem [`class-management design §4`](../class-management/class-management-design.md#4-rbac-helper-lõi-scoping)).
> **Admin bypass** (toàn quyền). Cột "Teacher" ✅ dưới đây nghĩa là *"cho HS thuộc lớp
> teacher dạy"*; với HS ngoài lớp → **403**.
>
> *(Trước class-management, luật là "any teacher" — đã thay. Implement check này ở phase
> teacher-grading, cùng với amend attempt detail trong attempt-lifecycle.)*

### 6.1 Ai làm gì

| Action | Student (owner) | Teacher (lớp mình) | Admin | Parent (linked) |
|---|:-:|:-:|:-:|:-:|
| View writing/speaking answers (post-submit) | ✅ | ✅ | ✅ | ✅ |
| View teacher comments | ✅ | ✅ | ✅ | ✅ |
| Grade writing/speaking | ❌ | ✅ (HS trong lớp) | ✅ | ❌ |
| Add/edit/delete writing-range comments | ❌ | ✅ (HS trong lớp) | ✅ | ❌ |
| Set/clear speaking-overall comment | ❌ | ✅ (HS trong lớp) | ✅ | ❌ |
| Grade/comment bài của HS **ngoài lớp** | ❌ | ❌ **403** | ✅ | ❌ |
| Re-grade / re-comment sau fully-graded | ❌ | ❌ (v2) | ❌ (v2) | ❌ |

### 6.2 Visibility theo giai đoạn

- **Post-submit, pre-graded**: student thấy câu + đáp án mình + 0 điểm cho writing/speaking; cờ `false`. Teacher thấy thêm input chấm. Parent thấy như student (data-parity per [`PARENT.md §3`](../../PARENT.md)).
- **Comment hiện ngay**: student/parent thấy comment **ngay khi** teacher thêm (trước fully-graded) — refresh là thấy feedback từng phần.
- **Fully graded**: mọi viewer thấy điểm chốt per-câu + tổng; writing/speaking có `isCorrect=true` + `pointsEarned`.

### 6.3 Speaking recording URL

URL trong `student_recordings` là public (ai có URL đều nghe/xem được). Chi tiết +
trade-off thuộc writing-speaking design; FE chỉ hiện URL cho viewer hợp lệ.

---

## 7. Lock — grades & comments freeze

Grade và comment khóa **cùng lúc**: khi `is_fully_graded` lật `false→true`.

Sau đó:
- POST/PUT/DELETE grade → 400 `already fully graded`.
- POST/PUT/DELETE comment → 400 `comments locked`.
- GET vẫn chạy — data read-only vĩnh viễn.

Re-grade/re-comment dưới audit chặt → v2 ([§10](#10-future-v2-roadmap)).

---

## 8. Migration plan (phần teacher-grading)

> Schema teacher-grading nằm trong **`migrations/0012_writing_speaking_full.sql`** (đã apply,
> gồm cả prod). Dưới đây chỉ phần thuộc teacher-grading; phần type CHECK + `is_fully_graded`
> ở writing-speaking design §9.

```sql
-- writing_comments table (xem §4.2)
CREATE TABLE IF NOT EXISTS public.writing_comments ( ... );
CREATE INDEX IF NOT EXISTS writing_comments_answer_id_idx
  ON public.writing_comments (answer_id, range_start);

-- speaking comment inline columns (xem §4.2)
ALTER TABLE public.answers
  ADD COLUMN IF NOT EXISTS speaking_comment      text,
  ADD COLUMN IF NOT EXISTS speaking_comment_by   uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS speaking_comment_at   timestamptz;
```

`schema.sql` update lockstep.

---

## 9. API changes summary

### 9.1 New endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/api/teacher/attempts/{id}/grade` | Chấm điểm writing/speaking | Teacher/Admin |
| `POST` | `/api/teacher/attempts/{id}/answers/{aid}/writing-comments` | Thêm range comment | Teacher/Admin |
| `PUT` | `/api/teacher/attempts/{id}/answers/{aid}/writing-comments/{cid}` | Sửa text comment | Teacher/Admin |
| `DELETE` | `/api/teacher/attempts/{id}/answers/{aid}/writing-comments/{cid}` | Xóa comment | Teacher/Admin |
| `PUT` | `/api/teacher/attempts/{id}/answers/{aid}/speaking-comment` | Set/overwrite comment speaking | Teacher/Admin |
| `DELETE` | `/api/teacher/attempts/{id}/answers/{aid}/speaking-comment` | Clear comment speaking | Teacher/Admin |

### 9.2 Modified endpoints

| Endpoint | Change |
|---|---|
| `GET /api/attempts/{id}` | Mỗi writing answer mang `writingComments:[...]`; mỗi speaking answer mang `speakingComment:{...}\|null` (xem [§4.5](#45-response-trong-get-apiattemptsid)) |

> `isFullyGraded` trên view/history/submit do writing-speaking sở hữu (set lúc submit); teacher-grading sở hữu việc lật `true` trong response `POST /grade`.

### 9.3 Schemas

```python
# api/teacher/schemas.py
GradeItem(questionId: UUID, pointsEarned: int)          # 0 ≤ x ≤ question.points
GradeRequest(grades: list[GradeItem])                   # non-empty
GradeResponse(graded, attemptScore, attemptTotalPoints, attemptPercentage, isFullyGraded)
WritingCommentCreate(rangeStart, rangeEnd, quotedText, commentText)
WritingCommentUpdate(commentText)
WritingCommentView(id, rangeStart, rangeEnd, quotedText, commentText, createdBy, createdAt, updatedAt)
SpeakingCommentUpdate(commentText)
SpeakingCommentView(commentText, createdBy, createdAt)  # không updatedAt
```

`AnswerView` thêm `writingComments: list[WritingCommentView]` và
`speakingComment: SpeakingCommentView | None`.

---

## 10. Future v2 roadmap

- **Attempt-level overall comment** — `attempts.teacher_overall_feedback`: 1 comment cho cả bài (khác per-answer comment v1). Vd "good effort, work on past tense".
- **Audio feedback từ teacher** — teacher ghi audio, upload bucket `teacher_feedback`, gắn URL vào comment. Song song writing-speaking §8.
- **Re-grading & re-commenting sau fully-graded** — cần audit log (ai/khi nào/old→new). Email: re-send E5 "Bài đã cập nhật chấm/nhận xét".
- **Partial-credit tinh vi** cho câu auto-graded.

---

## 11. Appendix: files to change

> Phần lớn đã tồn tại (xem callout 🛠️ đầu doc). Bảng dưới đánh dấu ✅ = đã có / ⏳ = cần làm.

### 11.1 Đã tồn tại (chỉ THÊM class-scoping, không tạo mới)

| File | Trạng thái |
|------|-----------|
| `api/teacher/__init__.py`, `api/teacher/routes.py`, `api/teacher/schemas.py` | ✅ Có — `POST /grade` + 5 comment endpoints + schemas |
| [services/grading_service.py](../../services/grading_service.py) | ✅ Có — **thêm** check `class_service.teacher_shares_class_with` (admin bypass) |
| [services/comment_service.py](../../services/comment_service.py) | ✅ Có — **thêm** cùng check |
| [services/attempt_service.py](../../services/attempt_service.py) | ✅ Có — `get_attempt_with_answers` đã fetch comments; **thêm** scoping cho teacher viewer (attempt-lifecycle §5.7) |
| [api/attempts/schemas.py](../../api/attempts/schemas.py), [api/attempts/routes.py](../../api/attempts/routes.py) | ✅ Có — `AnswerView` đã có `writingComments`/`speakingComment` + map |
| [services/class_service.py](../../services/class_service.py) | ✅ Có — helpers `teacher_shares_class_with` / `teacher_teaches_class` sẵn sàng |
| `migrations/0012_*.sql` + `schema.sql` | ✅ Đã apply (gồm prod) — `writing_comments` + `answers.speaking_comment*` |
| [main.py](../../main.py) | ✅ Đã mount `api.teacher.router` |

### 11.2 Chưa làm (đợt sau / phụ thuộc feature khác)

| File | Trạng thái |
|------|-----------|
| `services/email_templates/student_graded.py` | ⏳ E5 template — chờ **email feature** build; hiện route chỉ log ([§5](#5-email-notification-on-full-grading-e5)) |

### 11.3 No new env vars

Reuse email infra (`docs/email/`) cho E5.
