---
feature: attempt-highlights
status: approved        # B1+B2 chốt, chưa implement
type: design
last-updated: 2026-06-07
author: Backend team
depends-on: [attempt-lifecycle, class-management]
---

# Attempt Highlights — Highlight + note khi làm bài (Design)

> **Scope**: học sinh **highlight bất kỳ text-run nào hiển thị cho mình** khi làm 1 attempt
> (passage, instructions, stem, option, đề writing/speaking, bài viết của chính mình…), kèm
> **1 note tùy chọn** mỗi highlight. Giáo viên **xem được** khi review (khách yêu cầu: muốn
> thấy HS đánh dấu/ghi chú gì).
>
> Cùng feature: [Frontend notes](attempt-highlights-frontend.md) · [Test cases](attempt-highlights-testcases.md).
> Chi tiết request/response xem **Swagger**.
>
> **Ripple: rất nhẹ** — độc lập với chấm điểm/strip/audio/exam-mode. Chỉ thêm 1 bảng +
> nhúng `highlights[]` vào payload attempt + reuse RBAC viewer sẵn có.

---

## 1. Overview & sub-features (B1)

- Anchor 1 highlight = **`(targetKey, rangeStart, rangeEnd, quotedText)`** + `note?` + `color?`.
- **`targetKey` opaque với BE** — BE **không** resolve/validate substring (khác `writing_comments` của teacher). Lý do: nội dung đề **bất biến trong attempt** → offset ổn định; resolve từng shape `question_data`/material là coupling nặng + giòn.
- Chỉ highlight **phần HS thấy** (sau `strip_correct`): KHÔNG có `correct_index`/`correct_answers`/`exampleAnswer`/audio `meta.transcript`… (HS không thấy → không tồn tại trong payload).

| # | Sub-feature | Mô tả |
|---|---|---|
| 1 | Tạo highlight | HS bôi 1 đoạn → POST `(targetKey, start, end, quotedText, note?, color?)`. Cho **chồng nhau**. |
| 2 | Note 1:1 | Mỗi highlight gắn tối đa 1 note (free text, tùy chọn). |
| 3 | Sửa / xóa | Sửa note/color (PATCH); xóa nguyên highlight theo id (DELETE). |
| 4 | Render lại | `highlights[]` nhúng vào **resume + detail** để FE áp lại. |
| 5 | Teacher review | Người xem được attempt (owner + teacher-scoped + admin + parent) thấy `highlights[]` (kèm note). |

**Không thuộc v1:** xóa một phần (sub-range) 1 highlight; highlight carry-over giữa các attempt; BE validate substring.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Loại bỏ | Lý do |
|---|-----------|-------------------|---------|-------|
| 1 | Anchor | **`targetKey` (opaque) + offset ký tự + quotedText** | Global offset / DOM-XPath / token-ID / chèn `<mark>` | Content bất biến trong attempt → offset đủ; opaque tránh coupling shape + tránh bẫy UTF-16(JS) vs codepoint(Py). |
| 2 | Lưu trữ | **1 row / highlight** (`attempt_highlights`) | JSONB array trên `attempts` | CRUD per-highlight; load 1 query có index; không read-modify-write. |
| 3 | FK | **Chỉ `attempt_id` (CASCADE)**; `targetKey` không FK tới question/section | FK tới question | `targetKey` opaque đồng nhất cho passage/answer/instruction; soft-delete question → highlight chỉ "không render". |
| 4 | Note | **1 note tùy chọn / highlight** | Không note / note tách bảng | Khách yêu cầu; 1:1 đơn giản. |
| 5 | Visibility | **Theo người xem được attempt** (owner + teacher-scoped + admin + parent) — **note hiện luôn** | Riêng tư chỉ HS | Khách: GV muốn xem HS đánh dấu/ghi chú gì. |
| 6 | Vòng đời mutation | **Tạo/sửa/xóa chỉ khi `in_progress`** (owner) | Cho sửa sau nộp | Highlight là thao tác lúc làm bài; sau nộp read-only. |
| 7 | Overlap | **Cho chồng nhau, không validate** | Cấm overlap (như teacher-comment) | Highlight cá nhân, không phải feedback chấm điểm. |
| 8 | Phạm vi lưu | **Per-attempt** | Per (user, exam) | "Khi làm bài (attempt)"; làm lại đề = highlight mới. |

---

## 3. DB schema — migration 0017

```sql
-- migrations/0017_attempt_highlights.sql — additive, idempotent
CREATE TABLE IF NOT EXISTS public.attempt_highlights (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id  uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
    target_key  text NOT NULL,               -- §4 grammar (opaque với BE)
    range_start int  NOT NULL CHECK (range_start >= 0),
    range_end   int  NOT NULL,
    quoted_text text NOT NULL,               -- snapshot đoạn bôi
    note        text,                        -- tùy chọn, 1 / highlight
    color       text,                        -- tùy chọn
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (range_end > range_start)
);
CREATE INDEX IF NOT EXISTS attempt_highlights_attempt_idx
  ON public.attempt_highlights (attempt_id);

ALTER TABLE public.attempt_highlights ENABLE ROW LEVEL SECURITY;
-- (Đồng bộ pattern mọi bảng khác; app ghi/đọc qua service_role.)
```
- `schema.sql` update lockstep.
- Không index `target_key` (load luôn theo `attempt_id`, lọc/nhóm phía app/FE).
- `quoted_text`/`note` nên cap độ dài ở tầng schema Pydantic (vd ≤ 4KB / 2KB).

---

## 4. `target_key` grammar (FE↔BE convention; BE opaque)

```
section:{sectionId}:instructions
material:{sectionId}:{idx}:content      (hoặc :label / :alt)
question:{questionId}:stem
question:{questionId}:option:{i}
question:{questionId}:prompt            (writing/speaking đề bài)
question:{questionId}:label|prefix|postfix   (form_completion)
answer:{questionId}                      (bài viết HS)
```
- BE **không parse** — chỉ lưu/trả nguyên chuỗi. FE chịu trách nhiệm map `targetKey` ↔ element + tính offset trên **chuỗi nguồn** (xem [frontend notes](attempt-highlights-frontend.md)).
- `[rangeStart, rangeEnd)` = offset ký tự trên chuỗi nguồn của text-run đó (semantics như `String.substring`).

---

## 5. API + RBAC

Mount dưới router attempts (cùng `api/attempts/`), prefix `/api/attempts/{attempt_id}/highlights`.

**Chỉ có endpoint GHI (mutation) — đọc đi qua embed (không có GET list riêng):**

| Method | Path | Ai | Hành vi & lỗi |
|---|---|---|---|
| POST | `/api/attempts/{id}/highlights` | **owner + in_progress** | `{targetKey, rangeStart, rangeEnd, quotedText, note?, color?}` → **201** highlight (trả nguyên item). 403 nếu không phải owner; 400 nếu attempt đã submit/abandon (không `in_progress`); 404 attempt. |
| PATCH | `/api/attempts/{id}/highlights/{hlId}` | **owner + in_progress** | Sửa `{note?, color?}` → 200 (trả item). 404 nếu highlight không thuộc attempt/owner. |
| DELETE | `/api/attempts/{id}/highlights/{hlId}` | **owner + in_progress** | → **204**. 404 nếu không tồn tại/không phải owner. |

> **Vì sao không có `GET /highlights`:** đọc đã đủ qua **embed** (resume/detail) + mỗi mutation **trả về item** vừa tạo/sửa → FE không cần fetch list riêng. Bỏ đi cho gọn + tránh lặp RBAC 4-nhánh. Thiết kế = **"đọc-nhúng, ghi-tách"** (write atomic từng highlight, không read-modify-write cả mảng).

**Nhúng vào payload sẵn có (để render 1 lần) — đây là đường ĐỌC duy nhất:**
- **Resume** (`start` Case B) + **GET detail** `/api/attempts/{id}`: thêm `highlights[]` (cùng shape item). Cần thêm field `highlights` vào `AttemptStartResponseData` (Case A trả `[]`, Case B trả đủ) + `AttemptDetailData`.
- Start Case A (đề mới) → `highlights: []`.

> ⚠️ **Nguồn-text để render in-context khác nhau giữa "đang làm" và "review":**
> - **Đang làm (start/resume):** payload có **đủ exam tree** (sections + `instructions` + materials `content` + questions) → highlight render in-context **mọi targetKey**.
> - **Review (GET detail):** detail chỉ trả `exam={id,title,level,skill}` + `answers[]` (có `question_data`: stem/options/prompt + `student_answer`) — **KHÔNG** có `instructions`/material `content`/passage. Nên trên review:
>   - Highlight `question:*` / `answer:*` → vẫn render in-context (nguồn nằm trong `answers[]`).
>   - Highlight `section:*:instructions` / `material:*` (passage) → **không có chuỗi nguồn trong detail** → FE hiển thị dạng **danh sách snippet** (`quotedText` + `note`), HOẶC tự lấy passage qua endpoint sẵn có `GET /api/exams/{id}?include=sections` rồi overlay.
> - **Quyết định:** v1 **không mở rộng detail** để gánh materials (giữ payload gọn); `highlights[]` luôn kèm `quotedText` đủ để GV/HS "thấy đã bôi gì + note" dạng snippet. FE muốn in-context passage lúc review thì gọi exam-include.

**RBAC:**
- **Mutation (POST/PATCH/DELETE):** chỉ **chủ attempt** (so `attempt.user_id`), và attempt **`in_progress`** (`submitted_at IS NULL AND NOT is_abandoned`) → else 403/400.
- **Read (qua embed):** không có route đọc riêng → quyền đọc **đi theo đúng RBAC của payload chứa nó**:
  - `highlights[]` trong **detail** → đã được route `get_attempt_detail` gác sẵn (owner / admin / parent `is_child_of` / teacher `teacher_shares_class_with`). Nhúng vào sau khi RBAC pass nên **không lộ** cho người không có quyền.
  - `highlights[]` trong **resume** → start route vốn **chỉ owner** (resume bài của chính mình).
  → **Không cần** helper authorize riêng cho highlights.

---

## 6. Không đổi / ripple

- Không đụng: chấm điểm, `strip_correct`, audio play, exam-mode, class scoping.
- Chỉ chạm: +bảng `attempt_highlights`; +`highlights[]` vào resume + detail serializer; +1 nhóm route.
- Teacher review tự thừa hưởng scoping lớp (class-management) — không thêm luật mới.

---

## 7. Appendix — files to change

| File | Change |
|------|--------|
| `migrations/0017_attempt_highlights.sql` | **New** — bảng + index (§3) |
| `schema.sql` | Mirror bảng `attempt_highlights` |
| `services/highlight_service.py` *(mới)* | create / update / delete (owner + in_progress) + `list_for_attempt` (dùng cho embed) |
| `services/attempt_service.py` | Nhúng `highlights[]` vào resume payload + detail dict (gọi `list_for_attempt`) |
| `api/attempts/routes.py` + `schemas.py` | **3 route ghi** POST/PATCH/DELETE highlights (KHÔNG có GET list) + schema; thêm field `highlights[]` vào `AttemptStartResponseData` + `AttemptDetailData` |
| `tests/` | xem [test cases](attempt-highlights-testcases.md) |

---

## 8. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| Admin sửa đề đang có attempt → offset lệch | Cấm sửa đề đã publish có attempt (quy ước); `quotedText` cho FE re-anchor/bỏ qua |
| ⭐ **`answer:*` (bài viết HS) là text MUTABLE** — khác content đề (bất biến). HS highlight bài viết rồi **sửa tiếp** → offset lệch | Chấp nhận ở v1: `quotedText` làm lưới — FE so `quotedText` với answer hiện tại, **lệch thì tự dò lại / bỏ render** highlight đó. (Content đề thì không có vấn đề này.) Cân nhắc v2: FE chỉ cho highlight bài viết ở **chế độ review** (answer đã đông cứng) — nhưng review lại là read-only nên mâu thuẫn vòng đời; để v2 quyết. |
| Question/section soft-delete | `targetKey` không resolve → FE bỏ render lặng lẽ |
| Offset UTF-16 (JS) vs codepoint (Py) | BE opaque, không slice → không có vấn đề; FE nhất quán code-unit |
| Note riêng tư bị GV thấy | Đã chốt cho GV thấy (yêu cầu khách); ghi rõ ở FE để HS biết note không riêng tư |
| Selection cắt giữa `{{gap:N}}` | FE **snap biên ra mép token** (xem frontend §gap) |
| Spam highlight nhiều row | Index `(attempt_id)`; cap độ dài note/quoted_text; (tùy chọn) cap số highlight/attempt sau |
