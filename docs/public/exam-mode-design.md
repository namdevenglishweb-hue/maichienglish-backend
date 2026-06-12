---
feature: exam-mode
status: approved        # B1+B2 chốt, chưa implement
type: design
last-updated: 2026-06-06
author: Backend team
depends-on: [attempt-lifecycle]
---

# Exam Mode — Thi thử / Thi thật (Design)

> **Scope**: thêm **chế độ** cho mỗi attempt. **Thi thử (`practice`)** = hành vi hiện tại
> (không đổi gì). **Thi thật (`real`)** khác 2 điểm: (1) **mỗi audio chỉ nghe đúng 1 lần**
> (`max_audio_plays` hiệu lực = 1, bỏ qua cấu hình section); (2) **không resume/tạm dừng** —
> attempt real bị gián đoạn coi như **bỏ (abandon)**.
>
> Cùng feature: [Frontend notes](exam-mode-frontend.md) · [Test cases](exam-mode-testcases.md).
> Chi tiết request/response xem **Swagger**.
>
> **Ảnh hưởng feature đã có:** đây là **mở rộng attempt-lifecycle** — chạm `start_attempt`,
> `record_audio_play`, payload exam-tree. **Không** đổi strip đáp án, chấm điểm, scoping lớp.

---

## 1. Overview & sub-features (B1)

- **Học viên chọn chế độ lúc bắt đầu** (per-attempt). Cùng 1 đề làm được cả thi thử lẫn thi thật.
- Cờ lưu ở `attempts.mode` (`practice` | `real`), default `practice`.

| # | Sub-feature | Mô tả |
|---|---|---|
| 1 | Chọn chế độ khi start | `POST /api/attempts` nhận `mode`; lưu vào `attempts.mode`. Default `practice`. |
| 2 | Ép audio = 1 (real) | `record_audio_play` dùng cap hiệu lực = 1 khi `real`; payload báo `maxAudioPlays = 1`. |
| 3 | No-resume (real) | Attempt real **không resume**; mở/start lại khi đang có real active ⇒ **abandon** đề cũ rồi tạo mới. |
| 4 | Nhãn chế độ | `mode` trên attempt object ở `/active`, `/{id}` detail, history (start response chỉ có `attemptId` — FE tự biết mode vì vừa gửi). FE hiển thị "Thi thật". |

**Không thuộc phạm vi v1:** ẩn đáp án sau khi nộp (real **vẫn** hiện đáp án như thi thử); timer/đếm giờ riêng cho real; thống kê tách riêng real vs practice.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Đặt cờ ở đâu | **`attempts.mode`** (per-attempt, chọn lúc start) | Cột trên `exams` (đề thật/đề thử) | Cùng 1 đề luyện được cả 2 chế độ; "attempt khác nhau" đúng bản chất. |
| 2 | Giá trị cap audio (real) | **Cứng = 1** mỗi audio | Cho admin cấu hình cap riêng cho real | Yêu cầu rõ "nghe 1 lần"; gọn, không thêm cấu hình. |
| 3 | Nơi enforce cap | **`record_audio_play`** (1 chỗ tính `max_plays`) | Thêm bảng/route riêng | Tái dùng counter + index sẵn có; thay đổi tối thiểu. |
| 4 | No-resume enforce ở đâu | **Tại `start_attempt`**: real active ⇒ force-abandon rồi tạo mới | Auto-abandon mọi lần GET active/detail | Không phá refresh trong 1 phiên; vẫn đảm bảo "không tiếp tục đề cũ". |
| 5 | Gián đoạn real | **Abandon (score 0)** | Auto-submit chấm phần đã làm | Khách chốt: "abandon luôn" — đúng tinh thần thi thật một lần. |
| 6 | Đáp án sau nộp (real) | **Giữ nguyên** (hiện như thi thử) | Ẩn đáp án với real | Khách chỉ yêu cầu audio=1 + no-resume; không đụng strip. |
| 7 | Tương thích ngược | **`mode` default `practice`** | Bắt buộc client gửi mode | Attempt cũ + client cũ không gửi `mode` vẫn chạy y như trước. |

---

## 3. DB schema — migration 0016

```sql
-- migrations/0016_attempt_mode.sql — additive, idempotent
ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'practice'
    CHECK (mode IN ('practice','real'));
```

- Additive, không backfill: attempt cũ nhận default `practice`.
- `schema.sql` update lockstep (thêm cột `mode` vào `CREATE TABLE public.attempts`).
- **Không** đổi index `attempts_one_active_per_user` (vẫn "1 active/user", mode không phá luật này).

---

## 4. Start flow — chọn mode + no-resume (real) ⭐

Sửa `attempt_service.start_attempt(user_id, exam_id, mode="practice")`. Bảng quyết định (mode-aware):

| Active hiện tại | Mode yêu cầu | Hành vi |
|---|---|---|
| (không có) | practice / real | **Tạo mới** với mode yêu cầu (Case A). |
| **`real`** (đề bất kỳ) | practice / real | **Force-abandon** active đó (`is_abandoned=true`, `submitted_at=now()`, score 0 — cùng cơ chế nút abandon) → **tạo mới**. **Real không bao giờ resume.** |
| `practice`, **cùng đề** | practice | **Resume** (Case B) — giữ nguyên. |
| `practice`, **cùng đề** | **real** | **409** — "hoàn tất/bỏ bài thi thử đang dở trước" (không nuốt ý định real). |
| `practice`, **khác đề** | practice / real | **409** (Case C) — giữ nguyên. |

> Tóm: real active luôn bị abandon khi start mới; muốn lên real thì **không được đang có** practice dở (else 409). Practice cùng đề + yêu cầu practice vẫn resume như cũ.

INSERT thêm cột `mode`:
```sql
INSERT INTO public.attempts (user_id, exam_id, mode) VALUES ($1, $2, $3) RETURNING ...
```

**Ghi chú lifecycle:**
- Trong **1 phiên liên tục**: save answers / record audio / submit theo `attempt_id` chạy bình thường (không có xử lý đặc biệt) — "no-resume" chỉ enforce ở **chỗ start**.
- Mở lại đề real từ history / list ⇒ gọi start ⇒ đề real cũ bị abandon ⇒ bắt đầu mới (mất tiến độ + tính 1 lượt quota). Đây là chủ đích "thi thật một lần".
- Race-safe: nhánh `UniqueViolationError` (mất race INSERT) re-resolve theo đúng bảng trên.

---

## 5. Audio enforcement — ép cap = 1 (real)

Sửa `attempt_service.record_audio_play` ([attempt_service.py §record_audio_play](../../services/attempt_service.py)):

1. Đọc `attempts.mode` của attempt (join hoặc fetch kèm khi load attempt/section).
2. **`max_plays_effective = 1 if mode == 'real' else section.max_audio_plays`**.
3. Sau khi increment, reject khi `max_plays_effective is not None AND new_count > max_plays_effective` → `AudioPlayLimitExceededError`.

- Real ⇒ cap = 1 **bất kể** `section.max_audio_plays` (kể cả khi section để 3 hoặc `null`/không giới hạn).
- Practice ⇒ y như hiện tại (theo `section.max_audio_plays`).
- `remaining = max_plays_effective - new_count` (real: còn 0 sau lần đầu).

---

## 6. Payload — báo cap + nhãn cho FE

**`maxAudioPlays` per section** (`AttemptSectionView`): override = **1 khi attempt `real`**; ngược lại = `section.max_audio_plays`. Override trong `_fetch_exam_tree(conn, exam_id, mode)`. Áp ở **start (Case A)** — nơi exam-tree được trả. *(Resume Case B cũng qua `_fetch_exam_tree` nhưng chỉ practice; real không resume.)* ⚠️ **GET detail KHÔNG mang `maxAudioPlays`** (chỉ trả `audio_play_counts`) → không cần override ở detail; FE đã có cap từ payload start. BE quyết nghĩa, FE chỉ render.

**Nhãn `mode`** — thêm field `mode` (`"practice"`/`"real"`) vào các schema **mang attempt object**:
- `AttemptView` (dùng ở **history** `GET /api/attempts/history`).
- `ActiveAttemptData` (dùng ở **`GET /api/attempts/active`**).
- `AttemptDetailData` (dùng ở **`GET /api/attempts/{id}`**).
- ⚠️ **Start response** (`AttemptStartResponseData`) hiện chỉ trả `attemptId` (không có attempt object đầy đủ) → FE đã **biết mode từ chính request của mình**; tuỳ chọn thêm `mode` vào đây cho đủ. Cần kèm `mode` vào dict service trả (`get_active_attempt`, `list_history_for_user`, detail, `_row_to_attempt`) + thêm `mode` vào `_ATTEMPT_COLS` (RETURNING) để đọc được.

---

## 7. Không đổi (giữ nguyên)

- **Strip đáp án**: real vẫn theo luật state-based hiện tại (in_progress ẩn cho mọi người; submitted/abandoned hiện cho mọi người). Real **vẫn hiện** đáp án đúng sau khi nộp.
- **Chấm điểm / `is_fully_graded`**: không đổi (real chấm như practice).
- **Tier-limit / quota**: real attempt tính 1 lượt như practice (forfeited real cũng đã tính).
- **Scoping lớp, chấm writing/speaking, comment**: không đụng.
- **Index `attempts_one_active_per_user`**: giữ — vẫn 1 active/user.

---

## 8. Appendix — files to change

| File | Change |
|------|--------|
| `migrations/0016_attempt_mode.sql` | **New** — `ADD COLUMN mode` (§3) |
| `schema.sql` | Mirror cột `mode` trên `attempts` |
| [services/attempt_service.py](../../services/attempt_service.py) | `start_attempt(..., mode)` + no-resume (§4); `record_audio_play` cap hiệu lực = 1 nếu real (§5, vẫn raise `AudioPlayLimitExceededError`); `_fetch_exam_tree` override `maxAudioPlays` (§6); thêm `mode` vào `_ATTEMPT_COLS`/`_row_to_attempt`, `get_active_attempt`, `list_history_for_user`, detail dict |
| [api/attempts/schemas.py](../../api/attempts/schemas.py) | `AttemptStartRequest.mode` (`Literal["practice","real"]` default `practice`); thêm `mode` vào `AttemptView`, `ActiveAttemptData`, `AttemptDetailData` (tuỳ chọn `AttemptStartResponseData`) |
| [api/attempts/routes.py](../../api/attempts/routes.py) | start route truyền `mode`. *(Audio-limit vẫn map `PermissionDeniedError` → **403**, không đổi.)* |
| `tests/` | xem [test cases](exam-mode-testcases.md) |

---

## 9. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| Refresh giữa chừng đề real bị mất bài | "No-resume" chỉ enforce ở start; trong phiên thao tác theo attempt-id vẫn chạy. FE cảnh báo trước khi vào thi thật. |
| Client cũ không gửi `mode` | Default `practice` ⇒ hành vi y như trước. |
| Section để `max_audio_plays` lớn nhưng quên rằng real ép 1 | BE override cứng = 1 ở cả enforce lẫn payload ⇒ không phụ thuộc cấu hình section. |
| Nhầm tưởng real ẩn đáp án | Doc nêu rõ v1 real **không** ẩn đáp án sau nộp (chỉ audio=1 + no-resume). |
