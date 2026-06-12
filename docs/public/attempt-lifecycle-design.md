---
feature: attempt-lifecycle
status: implemented        # backend + migration 0010 đã ship; FE integration pending
type: design
last-updated: 2026-06-02
author: Backend team
depends-on: [exams, questions, media-upload, session-management, teacher-grading]
---

# Attempt Lifecycle — Thiết kế & Core feature

> **Scope**: trọn vòng đời một lượt làm bài — start, save, resume, submit, abandon, và
> đếm lượt nghe audio. Bao gồm luật "1 active attempt globally", manual save, resume,
> abandon, per-audio replay tracking.
>
> **Source of truth** cho attempt subsystem. Bổ sung cho
> [`MAICHIENGLISH_BACKEND_PLAN.md`](../../MAICHIENGLISH_BACKEND_PLAN.md) §3.7–§3.8 / §4.7.
>
> **Trạng thái:** Backend + migration 0010 **đã implement**; FE integration pending.
>
> Tài liệu cùng feature: [Frontend notes](attempt-lifecycle-frontend.md) ·
> [Test cases](attempt-lifecycle-testcases.md). Chi tiết API xem **Swagger**.
>
> **Ranh giới với feature khác:** hết-giờ/timeout → `docs/session-management/` (§5.4 cũ
> được thay bằng feature đó); chấm writing/speaking → `docs/teacher-grading/` (ở đây chỉ
> auto-grade MC/fill/matching); upload audio đề → `docs/...media-upload` (ở đây chỉ *đếm
> lượt nghe*).

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [Database schema + migration 0010](#3-database-schema--migration-0010)
4. [State machine](#4-state-machine)
5. [API specification](#5-api-specification)
6. [Business rules](#6-business-rules)
7. [Edge cases & decisions](#7-edge-cases--decisions)
8. [Strip / visibility rule (state-based)](#8-strip--visibility-rule-state-based)
9. [Roadmap — exam versioning (post-v1)](#9-roadmap--exam-versioning-post-v1)
10. [Appendix: endpoints & files](#10-appendix-endpoints--files)

---

## 1. Overview

Quản lý trọn vòng đời một lượt làm bài: bắt đầu → lưu → tạm dừng/quay lại → nộp hoặc bỏ.
Khác với upload/translate, feature này có **state machine + ràng buộc DB race-critical +
luật hiển thị đa-actor**.

Trạng thái lượt: `in_progress` → (`submitted` | `abandoned`). Mỗi học sinh tối đa **1 lượt
active** (chưa submit, chưa abandon) tại một thời điểm trên mọi đề.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Giới hạn lượt active | **1 active globally, enforce bằng partial unique index DB** | App-code check | Race-safe ngay cả khi 2 request đồng thời; DB từ chối INSERT thứ 2. |
| 2 | Start lặp lại | **Idempotent** — đang làm dở đúng đề đó → trả lượt cũ (200, không tốn quota) | Luôn tạo mới | Tránh tạo trùng + tốn quota oan; resume liền mạch. |
| 3 | Nguồn trạng thái UI | **Client-driven, server authoritative** — FE cache `GET /active`, UI quyết theo cache; 409 chỉ là lưới an toàn race | FE phản ứng theo lỗi 409 | Errors chỉ mang message; data đi qua GET riêng. 409 không kèm data (không lộ exam đang active). |
| 4 | Lưu đáp án | **Explicit save (thủ công)** | Auto-save liên tục | Học sinh tự kiểm soát thời điểm persist. FE nên cảnh báo `beforeunload`. |
| 5 | Abandon | **Không hoàn tác, điểm 0, vẫn tính quota** | Hoàn quota khi abandon | Chống abuse (start→abandon vô hạn). |
| 6 | Submit | **Final** — chấm xong khóa, không sửa/nộp lại | Cho re-submit | Tính toàn vẹn bài thi. |
| 7 | Submit + saved | **Merge**: body override saved; saved không có trong body vẫn giữ & chấm | Body thay toàn bộ | Cho phép nộp khi đã lưu dở. |
| 8 | Ẩn đáp án đúng | **Strip iff `in_progress`** (submitted_at NULL); **submitted + abandoned → show** | Strip cả abandoned | Quyết định 2026-06-03 "làm đơn giản": đang làm không gợi ý; đã nộp/đã bỏ đều cho xem lại đáp án. (Xem [§8](#8-strip--visibility-rule-state-based).) |
| 9 | Strip theo viewer? | **Không** — strip thuần theo trạng thái, áp cho **mọi viewer** (kể cả admin/teacher) | Admin/teacher xem in_progress unstripped (W25 cũ) | Đơn giản; bỏ W25. Khớp đúng code hiện tại. |
| 10 | Audio counter | **Per-material, lưu jsonb keyed by stringified index** | Enrich material / parallel array | Khớp DB; thiếu key = 0 lượt; FE index theo `String(idx)`. |
| 11 | Grading khi đề đổi giữa chừng | **v1: chấm theo trạng thái câu hỏi HIỆN TẠI** (lossy) | Pin snapshot | v1 chấp nhận; exam versioning ([§9](#9-roadmap--exam-versioning-post-v1)) sẽ sửa. |

---

## 3. Database schema + migration 0010

`migrations/0010_attempts_one_active_per_user.sql` — 4 bước, idempotent:
1. Thêm cờ `is_abandoned boolean NOT NULL DEFAULT false`.
2. Backfill: gộp các active trùng (giữ mới nhất, abandon phần còn lại).
3. **Partial unique index** `attempts_one_active_per_user ON (user_id) WHERE submitted_at IS NULL AND NOT is_abandoned`.
4. **UPSERT key** `answers_attempt_question_key UNIQUE (attempt_id, question_id)` (gộp row trùng trước).

```sql
CREATE TABLE public.attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score numeric(6,2), total_points numeric(6,2), percentage numeric(5,2),
  time_spent_seconds int,
  is_abandoned boolean NOT NULL DEFAULT false,        -- NEW
  started_at timestamptz NOT NULL DEFAULT now(),
  submitted_at timestamptz
);
CREATE UNIQUE INDEX attempts_one_active_per_user
  ON public.attempts (user_id) WHERE submitted_at IS NULL AND NOT is_abandoned;

-- answers: thêm UPSERT key
CREATE TABLE public.answers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  question_id uuid NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  student_answer jsonb,
  is_correct boolean,        -- NULL khi save dở; set khi submit
  points_earned int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (attempt_id, question_id)    -- NEW
);
```

- `attempt_section_state` (audio counters jsonb + resume timestamps) — **không đổi** (đã có từ migration 0006).
- `schema.sql` update lockstep → `scripts/init_schema.py --drop` ra state khớp.

---

## 4. State machine

```
(none) ──POST /attempts (201, quota+1)──► in_progress
in_progress ──PATCH /answers──► in_progress (save, không đổi state)
in_progress ──POST /attempts {same exam} (200, no quota)──► in_progress (resume)
in_progress ──POST /submit──► submitted (graded)
in_progress ──POST /abandon──► abandoned (score=0)
submitted | abandoned ──► (có thể start lượt mới)
```

| State | `submitted_at` | `is_abandoned` | Resume? | Save? | Submit? |
|-------|:-:|:-:|:-:|:-:|:-:|
| in_progress | NULL | false | ✅ | ✅ | ✅ |
| submitted | set | false | ❌ | ❌ | ❌ |
| abandoned | set | true | ❌ | ❌ | ❌ |

> ⚠️ Lưu ý: **abandon set `submitted_at = now()`** (cùng is_abandoned=true) — vì strip
> thuần theo `submitted_at`, abandoned **hiện đáp án** giống submitted (chủ ý — [§8](#8-strip--visibility-rule-state-based)).

---

## 5. API specification

Mọi endpoint cần `Authorization: Bearer`. Envelope: `{status, data}` (success) / `{detail}` (error).

### 5.1 `POST /api/attempts` — Start (idempotent)
Body `{examId}`. 3 outcome:
- **Case A** — không có active → tạo mới, **201**, `isResume=false`, quota +1, trả full exam tree (sections→questions, đáp án đúng đã strip), `savedAnswers=[]`.
- **Case B** — active **cùng exam** → **200**, `isResume=true`, trả `savedAnswers` (để prefill) + `audioPlayCounts` mỗi section, `startedAt` gốc. `savedAnswers` được lọc bỏ question đã xóa (defense-in-depth).
- **Case C** — active **khác exam** → **409** `{detail}` (lưới an toàn race; **không** kèm data về exam đang active).
- Errors: 403 parent / 403 quota / 404 exam không tồn tại hoặc unpublished.

### 5.2 `GET /api/attempts/active`
Trả lượt in_progress hiện tại: `{attemptId, examId, examTitle, examLevel, examSkill, startedAt, savedAnswerCount}`. **404** `No active attempt` nếu không có. (Không kèm audioPlayCounts — đó là summary endpoint.)

### 5.3 `PATCH /api/attempts/{id}/answers` — Manual save
Body `{answers: [{questionId, studentAnswer}]}`. Gửi **tất cả** đáp án hiện tại (UPSERT, idempotent). **Không validate shape** lúc save (defer tới submit). `is_correct=NULL`, `points_earned=0`. `studentAnswer=null` để xóa đáp án. Errors: 403 not owner / 400 submitted / 400 abandoned / 404 / 404 questionId không thuộc exam (cả batch rollback).

### 5.4 `POST /api/attempts/{id}/submit`
Body `{answers, timeSpentSeconds}`. **Merge** body + saved (body override; saved không có trong body vẫn chấm). Body rỗng `{answers:[]}` → chấm phần đã lưu. Chấm từng câu → set `is_correct`, `points_earned` → update `score/total_points/percentage/submitted_at`. Trả `{score, totalPoints, percentage, submittedAt}`. Errors: 403 / 400 submitted / 400 abandoned / 404.

### 5.5 `POST /api/attempts/{id}/abandon`
Không body. Set `is_abandoned=true, submitted_at=now(), score=0`. Vẫn tính quota. Giải phóng slot active. Errors: 403 / 400 already submitted / 400 already abandoned / 404.

### 5.6 `POST /api/attempts/{id}/sections/{sid}/audio-play?materialIndex=N`
Đếm lượt nghe per-material, cap = `sections.max_audio_plays` (NULL = unlimited). Thêm check: abandoned → 400. Counter lưu ở `attempt_section_state.audio_play_counts` (jsonb keyed by stringified index). FE tự tính remaining = `maxAudioPlays - used`.

#### 5.6.1 Shape của `audioPlayCounts` — key động (đừng nhầm với placeholder Swagger)

Counter surface ở **2 chỗ, 2 shape khác nhau** (cả hai đều là dict **key động**):

**(a) `POST /api/attempts` — per-section, phẳng** (`AttemptSectionView.audioPlayCounts`, kiểu `dict[str,int]`): key = **materialIndex** (string), value = số lượt. Đây chính là dạng `{0:1, 2:2}` đã chốt:
```jsonc
"audioPlayCounts": { "0": 1, "2": 2 }   // audio[0] nghe 1 lần, audio[2] nghe 2 lần; index vắng = 0
```

**(b) `GET /api/attempts/{id}` — top-level, lồng 2 tầng** (`AttemptDetailData.audioPlayCounts`, kiểu `dict[str, dict[str,int]]`): vì detail **phẳng, không có mảng section** để gắn, nên phải khóa ngoài bằng **section_id**:
```jsonc
"audioPlayCounts": {
  "uuid-section-2": { "0": 1, "2": 2 },   // ngoài = section_id, trong = {materialIndex: count}
  "uuid-section-3": { "0": 3 }
}
```
Section/material vắng key ⇒ count = 0.

> ⚠️ **`additionalProp1/2/3` trên Swagger chỉ là PLACEHOLDER** cho dict key-động — Swagger
> không biết key thật (`section_id`, `materialIndex`) nên bịa tên. **Data thật không hề có
> chữ "additionalProp".** Đây không phải bug; shape đúng như 2 ví dụ trên.

### 5.7 `GET /api/attempts/{id}` — Detail
Trả `{attempt, exam, answers[], audioPlayCounts}`. **Strip đáp án thuần theo trạng thái** ([§8](#8-strip--visibility-rule-state-based)): `in_progress` → strip cho **mọi viewer**; `submitted` + `abandoned` → **visible** + `is_correct`/`points_earned` từng câu. **Quyền MỞ detail**: owner / admin / teacher / parent-of-linked-child.

> ✅ **Class-scoping cho teacher đã wire** (phase 2): trong `get_attempt_detail`
> ([api/attempts/routes.py](../../api/attempts/routes.py)), khi viewer là **teacher**
> (không phải admin/owner/parent), kiểm `class_service.teacher_shares_class_with(viewer_id,
> attempt.user_id)` → False thì **403**. Owner/admin/parent giữ nguyên. Strip đáp án
> **không đổi** (option A). (Trước đây là "teacher xem any" — đã siết.)

### 5.8 `GET /api/attempts/history`
Danh sách lượt đã kết thúc (submitted + abandoned, loại in_progress), order `started_at DESC`. Mỗi item có `isAbandoned`. (Pagination: locked ở test H9 — xem testcases.)

---

## 6. Business rules

### 6.1 Quota
- Tiêu quota **chỉ khi 201 Created** (resume 200 không tiêu).
- Abandon **không hoàn** quota; abandoned + submitted + in_progress đều **tính**.
- Tier: Free 5/tháng, Basic 50, Pro/Ultra unlimited.
- Counter: `COUNT(*) WHERE user_id=$1 AND started_at >= subscription.current_period_start`.
- **Caveat v1**: `current_period_start` set 1 lần lúc tạo sub, **chưa advance** → thực tế đếm từ lúc tạo sub. Period-reset là task riêng.
- Subscription `status='canceled'` → chặn start bất kể tier (locked Q12).

### 6.2 Concurrency
Partial unique index chặn 2 active đồng thời. Khi INSERT thứ 2 fail `UniqueViolationError`, service re-fetch active row → cùng exam = Case B (200), khác exam = Case C (409). Race-safe.

### 6.3 Exam lifecycle khi đang làm
- Admin unpublish/soft-delete exam → lượt hiện tại vẫn save/submit; **không** start mới được cho exam đó.
- Admin sửa câu hỏi → save dùng questionId (ổn định); submit chỉ iterate `WHERE deleted_at IS NULL`; câu soft-deleted bị skip (row đáp án còn nhưng không tính điểm); hard-delete CASCADE xóa row đáp án.
- ⚠️ Sửa `correct_index` giữa chừng → chấm theo key MỚI (lossy v1, locked W11b) → exam versioning sẽ sửa ([§9](#9-roadmap--exam-versioning-post-v1)).

### 6.4 Session timeout
→ Đã chuyển sang feature **session-management** (`docs/session-management/`). §5.4 cũ của doc này được thay bằng đó.

---

## 7. Edge cases & decisions

| # | Scenario | Quyết định |
|---|----------|-----------|
| 1 | F5 giữa bài chưa save | Resume trả existing (200); đáp án chưa lưu → input rỗng, phải làm lại. By design (save thủ công). FE nên `beforeunload`. |
| 2 | Start trên phone, save, mở laptop | Resume OK — `POST /attempts {same}` trả 200 + savedAnswers. |
| 3 | Abandon rồi start lại đúng đề | Tạo mới (201), quota +1 (tổng 2 cho đề này), lượt cũ ở history "Abandoned". |
| 4 | Free 5/tháng, abandon 5 lần | Lần 6 → 403. By design. |
| 5 | 2 tab cùng đề | Cả hai gọi POST /attempts → cùng attempt (idempotent); cùng save (last write wins); submit từ tab nào cũng finalize. |
| 6 | Admin sửa/xóa câu giữa chừng | Lượt không bị ảnh hưởng; submit chấm theo trạng thái hiện tại; CASCADE dọn orphan; resume lọc savedAnswers theo câu còn tồn tại. |
| 7 | Tier downgrade khi đang làm | Lượt hiện tại không ảnh hưởng; tier check chỉ ở start. |
| 8–9 | Mất mạng khi save | FE retry (UPSERT idempotent). `beforeunload` fail thì mất đáp án — trade-off của save thủ công; FE có thể backup localStorage. |
| 10 | Parent gọi POST /attempts | 403. |
| 11–12 | Cache active cũ / 409 | FE refresh `GET /active` rồi chạy lại logic; **không** parse data từ body 409. |

---

## 8. Strip / visibility rule (state-based)

**Quyết định 2026-06-03 (làm đơn giản):** ẩn/hiện đáp án đúng **thuần theo trạng thái
attempt**, KHÔNG phụ thuộc viewer.

| Trạng thái | Đáp án đúng (`correct_index`/`correct_answers`) |
|---|---|
| `in_progress` | **Ẩn** (strip) — cho **mọi** viewer, kể cả admin/teacher |
| `submitted` | **Hiện** + `isCorrect`/`pointsEarned` từng câu — cho **mọi** viewer, **kể cả chính học sinh (owner)** xem lại bài mình |
| `abandoned` | **Hiện** (không ẩn — làm đơn giản), cũng cho mọi viewer kể cả owner |

> **Học sinh xem lại bài đã nộp → THẤY đáp án đúng** (lựa chọn A — làm đơn giản). Đánh đổi
> đã chấp nhận: nếu đề tái sử dụng, học sinh có thể lộ/chia sẻ đáp án. Nếu sau này cần
> chặt hơn (ẩn đáp án riêng với học sinh) → quay lại strip-theo-viewer (v2).

Code hiện tại ([attempt_service.py](../../services/attempt_service.py) `get_attempt_with_answers`:
`if not is_submitted: strip`, viewer-agnostic) **đã khớp đúng** quyết định này: abandon set
`submitted_at` nên không bị strip (hiện đáp án); in_progress strip cho mọi người. **Không cần sửa code.**

> 2 "discrepancy" trước đây (V4: abandoned phải strip; W25: admin/teacher xem in_progress
> unstripped) **đã bị bỏ** theo quyết định trên — test V4/W25 đổi tương ứng (xem testcases).
> Đánh đổi đã chấp nhận: học sinh có thể bỏ bài → xem đáp án → làm lại (tốn thêm 1 quota).

---

## 9. Roadmap — exam versioning (post-v1)

W11/W11b chốt **lossy current-state grading** là hành vi v1: admin sửa `correct_index` →
lượt đang chạy chấm theo key mới → đáp án từng đúng thành sai. Không chấp nhận lâu dài.

**Mitigation (design doc riêng khi scope):**
1. Bảng `exam_versions(id, exam_id, version_number, sections_snapshot, questions_snapshot, created_at)`.
2. `attempts.exam_version_id` FK — capture lúc start.
3. Admin sửa đề đã publish → tạo version mới; lượt đang chạy giữ snapshot pinned.
4. Student mới nhận version mới nhất.
5. History detail load từ snapshot, không từ live tree.

Khi versioning ra: W11/W11b đảo (edit KHÔNG ảnh hưởng grading), thêm W11c + history-from-version tests.

---

## 10. Appendix: endpoints & files

### 10.1 Endpoints

| Method | Path | Thay đổi | Status codes |
|---|---|---|---|
| POST | `/api/attempts` | Idempotent; 201 new / 200 resume / 409 conflict (race-only) | 200,201,403,404,409 |
| GET | `/api/attempts/active` | Single source of truth active state | 200,404 |
| PATCH | `/api/attempts/{id}/answers` | Manual save (UPSERT, no grading) | 200,400,403,404 |
| POST | `/api/attempts/{id}/submit` | Merge saved + body | 200,400,403,404 |
| POST | `/api/attempts/{id}/abandon` | Mark abandoned, release slot | 200,400,403,404 |
| POST | `/api/attempts/{id}/sections/{sid}/audio-play` | Per-material counter | 200,400,403,404 |
| GET | `/api/attempts/{id}` | Detail + isAbandoned + audioPlayCounts | 200,403,404 |
| GET | `/api/attempts/history` | + isAbandoned | 200 |

### 10.2 Files (đã implement)

| File | Vai trò |
|------|---------|
| `migrations/0010_attempts_one_active_per_user.sql` | is_abandoned + partial unique index + answers UPSERT key |
| [services/attempt_service.py](../../services/attempt_service.py) | start/save/submit/abandon/audio/detail/active/history + `_fetch_audio_play_counts` |
| [api/attempts/routes.py](../../api/attempts/routes.py) | wiring 8 route |
| [api/attempts/schemas.py](../../api/attempts/schemas.py) | view/request/response models |
| [utils/grading_utils.py](../../utils/grading_utils.py) | `grade_question`, `strip_correct` |
