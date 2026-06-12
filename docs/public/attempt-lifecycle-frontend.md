---
feature: attempt-lifecycle
status: implemented
type: frontend
last-updated: 2026-06-02
author: Backend team
depends-on: [exams, questions]
---

# Attempt Lifecycle — Lưu ý cho Frontend

> Doc này **chỉ ghi điểm FE cần biết + ý nghĩa data**. **Chi tiết request/response API
> xem Swagger.** BE giải thích ý nghĩa; render là việc của FE.
>
> Bối cảnh: [Attempt-lifecycle design](attempt-lifecycle-design.md) ·
> [Test cases](attempt-lifecycle-testcases.md).

---

## TL;DR cho FE

Mô hình cốt lõi: **FE giữ một biến `activeAttempt` trong app state**, fetch 1 lần lúc load
qua `GET /api/attempts/active`, và **cập nhật tường minh sau mỗi lần start/submit/abandon**.
**Mọi quyết định UI đọc từ cache này** — KHÔNG phản ứng theo lỗi. Response 409 chỉ là lưới
an toàn cho cache cũ, không bao giờ là tín hiệu quyết định chính.

---

## 1. Role journey overview

| Role | Làm gì |
|------|--------|
| **Student** | Bắt đầu/tiếp tục bài → làm → **bấm Lưu** (không auto-save) → Nộp hoặc Bỏ. Xem lại bài **mình** đã nộp (đúng/sai + đáp án). |
| **Teacher** | Xem detail attempt của **HS thuộc lớp mình dạy** (class-scoped — xem `docs/class-management/`); HS ngoài lớp → 403. |
| **Admin** | Xem detail **bất kỳ** attempt (không bị scope). |
| **Parent** | Xem attempt của con đã liên kết. **Không** start được (403). |

## 2. Data semantics — vì sao có / dùng làm gì

| Data / hành vi | Ý nghĩa | Hệ quả cho FE |
|----------------|---------|----------------|
| **`activeAttempt` (từ `GET /active`)** | Lượt đang làm dở (hoặc 404 = không có). **Single source of truth** cho UI. | Cache lúc load + sau mỗi mutation. Drive nút "Bắt đầu"/"Tiếp tục", banner, dialog từ cache này. |
| **`isResume`** (trong response POST /attempts) | `false` = tạo mới (201); `true` = tiếp tục bài cũ (200). | `true` → prefill `savedAnswers`, hiện UI "tiếp tục". |
| **`savedAnswers`** | Các đáp án đã lưu trước đó (chỉ câu đã save; câu chưa save vắng mặt). Đã lọc bỏ câu bị xóa. | Dùng để prefill input. Mọi `questionId` trong đây chắc chắn còn trong exam tree. |
| **409 ở POST /attempts** | Đang có lượt active cho **đề khác** (race / cache cũ). **Body chỉ có `detail`, KHÔNG có data.** | **Đừng parse data từ 409.** Refresh `GET /active` → chạy lại logic switch-exam. |
| **`audioPlayCounts`** | Số lượt đã nghe mỗi audio, **dict key động**. 2 shape: POST /attempts → per-section phẳng `{materialIndex: count}` (vd `{"0":1,"2":2}`); GET detail → lồng `{sectionId: {materialIndex: count}}`. Thiếu key = 0 lượt. | Tính remaining = `maxAudioPlays - (counts[String(idx)] ?? 0)`; `null` ⇒ unlimited. ⚠️ `additionalProp1/2/3` trên Swagger chỉ là **placeholder** cho key động — key thật là `sectionId`/`materialIndex`, KHÔNG có "additionalProp". |
| **Strip đáp án đúng** | Chỉ **đang làm** (`in_progress`) mới ẩn `correct_index`/`correct_answers` (cho mọi viewer). **Đã nộp + đã bỏ** → hiện đầy đủ. | Mid-attempt không thấy đáp án là đúng; bài đã nộp/đã bỏ thì thấy. |
| **`isCorrect` / `pointsEarned` per answer** (GET detail post-submit) | Đúng/sai + điểm từng câu. | Dựng màn review: từng câu đúng/sai + đáp án đúng + điểm. |
| **`isAbandoned`** | Lượt bị bỏ (điểm 0). | History: badge "Đã bỏ" vs "%". |
| **`score/totalPoints/percentage`** | Điểm tổng (sau submit). | Hiển thị kết quả. |

## 3. Pattern bắt buộc — cache là single source of truth

1. **App load / sau login**: gọi `GET /api/attempts/active` 1 lần → lưu `activeAttempt` (404 → đặt `null` tường minh).
2. **Drive UI từ cache** (đồng bộ, không gọi API để quyết):
   - Không có active hoặc active là **đề này** → nút "Bắt đầu"/"Tiếp tục bài làm".
   - Active là **đề khác** → bấm sẽ mở **switch-exam dialog** (không gọi API để biết).
   - Banner "đang làm dở bài X" ở các trang ngoài.
3. **Cập nhật cache tường minh** sau start (từ response), sau submit (`null`), sau abandon (`null`).

## 4. Các luồng chính (ý nghĩa, không phải code)

- **Start / resume (cùng 1 path)**: gọi `POST /attempts {examId}` → cập nhật cache từ response; nếu `isResume` → prefill `savedAnswers` + toast "tiếp tục", ngược lại toast "bắt đầu".
- **Switch-exam dialog**: khi active là đề khác — hỏi "Tiếp tục bài cũ" / "Bỏ bài cũ, làm mới". Chọn làm mới → gọi `/abandon` cũ → cache `null` → start mới. **Quyết định bằng cache, không gọi API.** Nhắc người dùng: bỏ bài cũ = điểm 0 + vẫn tính quota.
- **Save (thủ công)**: gửi **tất cả** đáp án hiện tại qua `PATCH /answers` (UPSERT, idempotent). **Không auto-save.** Nên: (a) cảnh báo `beforeunload` khi có thay đổi chưa lưu; (b) backup `localStorage` để chống mất khi mạng rớt.
- **Submit**: `POST /submit` với đáp án hiện tại (override saved) → nhận điểm → cache `null` → sang trang kết quả.
- **409 recovery**: catch 409 → `GET /active` → nếu giờ thấy đề khác đang active thì mở switch-exam dialog; nếu hết xung đột thì retry start. **Không** đọc gì từ body 409.

## 5. Strip rules — FE thấy gì / không thấy gì

| Trạng thái lượt | Đáp án đúng (`correct_index`/`correct_answers`) trong `questionData` |
|------------------|----------------------------------------------------------------------|
| in_progress (đang làm) | **Ẩn** (strip) — cho **mọi** viewer, kể cả admin/teacher |
| submitted (đã nộp) | **Hiện** + kèm `isCorrect`/`pointsEarned` từng câu |
| abandoned (đã bỏ) | **Hiện** (giống submitted — làm đơn giản) |

> **Quy tắc đơn giản (quyết định 2026-06-03):** strip **thuần theo trạng thái** — chỉ
> `in_progress` mới ẩn đáp án (cho mọi người); `submitted` và `abandoned` đều hiện **cho
> mọi viewer, kể cả chính học sinh xem lại bài mình**. Xem design
> [§8](attempt-lifecycle-design.md#8-strip--visibility-rule-state-based).
>
> ⚠️ **Khi test**: gọi GET detail bài **đang làm** → KHÔNG thấy đáp án (đúng). Đáp án chỉ
> hiện ở bài đã nộp/đã bỏ.

## 6. Edge cases FE cần nắm

- **F5 giữa bài chưa lưu** → resume trả 200 nhưng input rỗng (save thủ công). Phải làm lại phần chưa lưu.
- **2 tab cùng đề** → cùng 1 attempt; lưu được cả hai (last write wins); submit từ tab nào cũng khóa.
- **Cache cũ** (abandon ở thiết bị khác) → worst case banner "tiếp tục" cũ; bấm vào → server trả 201 (mới) thay vì 200 → cập nhật cache từ response, không mất data. Có thể re-sync `GET /active` khi window focus (optional).
- **Audio**: sau `POST /audio-play`, cập nhật count local từ response (không cần fetch lại tree).

## 7. Error scenarios

| Status | Detail (chứa) | Khi nào | Xử lý |
|--------|---------------|---------|-------|
| 409 | `unfinished attempt for another exam` | Start khi đang có active đề khác (cache cũ) | Refresh `/active` → switch-exam dialog. Không parse body. |
| 403 | `Parents cannot start exam attempts` | parent start | Ẩn nút start với parent. |
| 403 | `Monthly attempt limit reached (N)` | quá quota | Hiện thông báo hết lượt tháng. |
| 404 | `Exam {id} not found` | đề không tồn tại/unpublished | Refresh danh sách. |
| 400 | `Attempt already submitted` / `already abandoned` | thao tác trên lượt đã kết thúc | Cache cũ — refresh `/active`. |
| 400 | `Attempt is abandoned` | save/audio trên lượt đã bỏ | Như trên. |
| 403 | `Not the owner of this attempt` | student thao tác nhầm bài người khác | Bug auth. |
| 403 | (scoping) | **teacher** xem detail HS **ngoài lớp** mình dạy (class-management) | Chỉ mở từ danh sách bài của lớp mình; admin không bị scope. |

---

> Mọi field/endpoint cụ thể khác: **Swagger** là nguồn chính xác.
