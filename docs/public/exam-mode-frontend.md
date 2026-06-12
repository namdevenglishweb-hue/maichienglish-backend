---
feature: exam-mode
status: approved
type: frontend
last-updated: 2026-06-06
author: Backend team
depends-on: [attempt-lifecycle]
---

# Exam Mode (Thi thử / Thi thật) — Lưu ý cho Frontend

> Doc này **chỉ ghi điểm FE cần biết + ý nghĩa data**. **Chi tiết request/response xem Swagger.**
> BE giải thích ý nghĩa; render là việc của FE.
>
> Bối cảnh: [Exam-mode design](exam-mode-design.md) · [Test cases](exam-mode-testcases.md).

---

## TL;DR cho FE

Khi **bắt đầu** 1 bài, học viên chọn **thi thử** (`practice`, mặc định) hoặc **thi thật** (`real`).
Khác biệt thi thật, **toàn bộ do BE quyết**, FE chỉ đọc data:
1. **Mỗi audio chỉ nghe 1 lần** — BE trả `maxAudioPlays = 1` cho mọi section nghe; cố nghe lần 2 → BE chặn.
2. **Không tạm dừng / resume** — rời đề rồi mở lại = BE **bỏ (abandon)** bài cũ, không cho tiếp.

Đáp án đúng sau khi nộp: **vẫn hiện như thi thử** (real không ẩn đáp án ở v1).

---

## 1. Role journey overview

| Role | Làm gì |
|------|--------|
| **Student** | Trước khi vào làm bài, chọn **Thi thử / Thi thật**. Thi thật: audio nghe 1 lần, không quay lại được nếu rời. |
| **Teacher / Admin / Parent** | Không chọn chế độ. Khi xem lại 1 attempt thấy `mode` để biết bài đó là thi thử hay thật. |

> ⚠️ Thi thật là "một lần": FE nên **cảnh báo trước khi vào** ("Thi thật: audio nghe 1 lần, không tạm dừng — rời đi sẽ mất bài"). Sau đó BE enforce phần còn lại.

## 2. Data semantics — vì sao có / dùng làm gì

| Data / hành vi | Ý nghĩa | Hệ quả cho FE |
|----------------|---------|----------------|
| **`mode` khi start** (`practice`/`real`) | Gửi kèm lúc tạo attempt (xem Swagger). **Bỏ trống = `practice`** (tương thích cũ). | Màn chọn chế độ → gửi `mode`. Không gửi cũng được (mặc định thi thử). |
| **`attempt.mode`** (`practice`/`real`) | Chế độ của attempt. Có trong response **`/active`**, **`/{id}` (detail)**, **history**. *(Response start chỉ trả `attemptId` — lúc start FE đã tự biết mode vì chính mình gửi.)* | Hiển thị nhãn "Thi thật"/"Thi thử"; quyết cảnh báo/CTA. |
| **`section.maxAudioPlays`** | Số lần được nghe mỗi audio của section. **Thi thật BE trả `1`** (bất kể cấu hình đề). | FE chỉ đọc & render số này; **đừng tự suy** từ mode. Thi thử có thể là số khác hoặc `null` (không giới hạn). |
| **`audioPlayCounts`** | Số lần đã nghe mỗi audio (per material). | So với `maxAudioPlays` để biết còn nghe được không; hết lượt → disable nút nghe. |
| **Chặn nghe quá lượt** | Cố ghi nhận 1 lần nghe vượt cap → BE trả lỗi (audio play limit). | Không phải bug — disable nút khi `count >= maxAudioPlays`; nếu vẫn gọi và bị chặn thì báo nhẹ. |
| **No-resume (real)** | Đề real không có "tiếp tục". Mở lại đề real đang dở (từ history/list) ⇒ BE **abandon** bài cũ rồi bắt đầu mới. | FE **không** hiện nút "Tiếp tục" cho attempt real. Cảnh báo rõ trước khi rời màn thi thật. |
| **Đáp án sau nộp** | Real vẫn hiện đáp án đúng sau khi nộp (như thi thử). | Màn review dùng chung, không cần phân nhánh theo mode. |

## 3. Luồng student — vào làm bài

1. Chọn đề → màn chọn **Thi thử / Thi thật** → start kèm `mode` (Swagger cho shape).
2. Thi thật: FE cảnh báo "1 lần, không tạm dừng" trước khi vào.
3. Trong khi làm: đọc `maxAudioPlays` + `audioPlayCounts` để khoá nút nghe khi hết lượt (thi thật = 1).
4. Nộp như bình thường. Review sau nộp giống thi thử.

## 4. Những điều FE KHÔNG cần lo / KHÔNG tự làm

- **Đừng tự ép audio = 1 theo mode** — BE đã trả `maxAudioPlays = 1`; FE chỉ render con số BE đưa.
- **Đừng tự "tiếp tục" đề real** — không có resume; mở lại là bài mới (BE tự abandon bài cũ).
- Không cần tính điểm/strip khác đi cho real — chấm + hiện đáp án giống thi thử.

## 5. Error scenarios

| Status | Khi nào | Xử lý |
|--------|---------|-------|
| **403** | Ghi nhận nghe vượt cap (thi thật: lần 2 cùng 1 audio) — `AudioPlayLimitExceeded` | Disable nút nghe khi `count >= maxAudioPlays`; nếu vẫn bị chặn → báo nhẹ "đã hết lượt nghe". |
| 409 | Start khi đang có **đề thi thử dở** (đề khác **hoặc** cùng đề mà bấm "Thi thật") | Nhắc học viên hoàn tất/bỏ bài thi thử đang dở trước. *(Đề **thi thật** đang dở thì start mới tự abandon nó — không 409.)* |
| 422 | `mode` sai giá trị (khác `practice`/`real`) | Chỉ gửi 2 giá trị hợp lệ; hoặc bỏ trống để mặc định. |

---

> Mọi field/endpoint cụ thể: **Swagger** là nguồn chính xác.
