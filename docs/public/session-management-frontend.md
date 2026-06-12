---
feature: session-management
status: approved
type: frontend
last-updated: 2026-06-02
author: Backend team
depends-on: [auth, attempts]
---

# Session Management — Lưu ý cho Frontend

> Doc này **chỉ ghi điểm FE cần biết + ý nghĩa data**. **Chi tiết request/response API
> xem Swagger.** BE giải thích ý nghĩa; cách hiển thị/đếm ngược/redirect là việc của FE.
>
> Bối cảnh: [Session-management design](session-management-design.md) ·
> [Test cases](session-management-testcases.md).

---

## TL;DR cho FE

FE phải làm **2 việc chủ động** vì BE cố ý không gửi tín hiệu:
1. **Part A:** tự **refresh token trước khi hết hạn khi user còn active**, và **catch 401 → đẩy về login**.
2. **Part B:** tự **tính & hiển thị deadline làm bài** (BE không gửi countdown), và **xử lý 400 "Exam time has expired"** + hiểu rằng `GET /active` trả 404 nghĩa là bài cũ đã tự kết thúc.

---

## 1. Role journey overview

| Role | Dính tới feature này thế nào |
|------|------------------------------|
| **Mọi user đã đăng nhập** (Part A) | Active liên tục → phiên gia hạn liền mạch (FE refresh ngầm). Idle > 30 phút → request kế tiếp 401 → FE đưa về màn login. |
| **Student đang làm bài** (Part B) | Có deadline = `startedAt + durationMinutes`. Quá giờ mà thao tác (save/submit/audio) → 400 + bài tự abandon (điểm 0). Quay lại sau khi hết giờ → `GET /active` đã 404 → bắt đầu lại từ đầu. |
| **Admin / Parent** xem attempt expired | Khi mở detail một attempt quá giờ → BE lazy-write auto-abandon → thấy `isAbandoned=true`. Không cần thao tác gì. |

## 2. Data semantics — vì sao có / dùng làm gì

| Data / hành vi | Ý nghĩa | Hệ quả cho FE |
|----------------|---------|----------------|
| **JWT access token `exp`** | Hết hạn sau **30 phút** (rút từ 60). Hết hạn = "30 phút không refresh", chỉ xảy ra khi user idle. | FE đọc `exp` để hẹn refresh trước hạn (xem §3). Không có cột `last_active_at` — server dựa hoàn toàn vào exp đã ký. |
| **401 trên request bất kỳ** | Token đã hết hạn (idle quá lâu hoặc bị thu hồi). | Catch 401 toàn cục → clear token → redirect `/login` (kèm lý do nếu muốn). |
| **Refresh token** | Sống 7 ngày, **không xoay** ở v1 (mỗi /refresh trả access mới, refresh token giữ nguyên). | FE chỉ cần lưu lại token trả về; đừng giả định refresh token đổi. |
| `attempt.startedAt` + `exam.durationMinutes` | Đủ để FE **tự tính deadline** = `startedAt + durationMinutes`. BE **không** gửi countdown/deadline riêng. | FE tự dựng đồng hồ đếm ngược từ 2 field này. |
| **400 "Exam time has expired"** (save/submit/audio) | Bài đã quá giờ; BE vừa **auto-abandon** (điểm 0, đáp án đã lưu giữ lại để audit, không chấm). Save/submit bị từ chối hoàn toàn. | Hiện thông báo "bài đã hết giờ/tự kết thúc"; clear cache attempt; điều hướng về trang chính. |
| **`GET /active` trả 404** | Không còn attempt active — **bao gồm** trường hợp bài cũ vừa hết giờ và bị BE lazy-write auto-abandon. | Coi 404 là "không có bài dở". Trường hợp "hết giờ" là **vô hình** với FE — không cần branch riêng. |
| **auto-abandon ≠ auto-submit** | Hết giờ → bài bị **bỏ** (score=0), KHÔNG nộp đáp án dở. | UI lần sau cho học sinh "bắt đầu lại", không hiện điểm phần làm dở. |

## 3. Part A — Proactive refresh (BẮT BUỘC)

- FE **phải refresh token trước khi hết hạn** *khi user còn active* (không đợi 401 mới refresh). Cách làm gợi ý: đọc `exp` của access token, hẹn refresh ~1 phút trước hạn; nếu lúc đó user **không** active thì **bỏ qua** (để token hết hạn → buộc re-login). "Active" do FE định nghĩa (vd có input event trong 30 phút qua).
- Nếu FE không refresh: token hết hạn, user thấy login ở hành động kế tiếp — đúng hành vi mong muốn cho idle.
- **Catch 401 toàn cục** → clear token + redirect login. Đây là cơ chế "30 phút idle = re-login".

## 4. Part B — Deadline & xử lý hết giờ

- **Tự tính & hiển thị countdown** từ `startedAt + durationMinutes`. Khi về 0, FE có thể: hiện overlay "hết giờ", gọi `GET /active` (sẽ 404) để xác nhận, disable nút Save/Submit.
- ⚠️ **Clock skew**: server là chuẩn. Nếu đồng hồ FE **nhanh hơn** server → FE hiện "0:00" nhưng server vẫn nhận save tới khi *đồng hồ server* qua deadline. Nếu FE **chậm hơn** → server từ chối trong khi FE còn hiện thời gian. → FE nên coi **response của BE là sự thật**, có thể kèm disclaimer nhỏ về đồng bộ giờ.
- **Xử lý 400 "Exam time has expired"**: hiện dialog "bài đã hết giờ, đã tự kết thúc", clear `activeAttempt` local, đưa về danh sách đề.
- **Resume dialog** (yêu cầu UX của bạn — "lần tới hỏi làm tiếp hay làm lại"): dựng hoàn toàn từ `GET /active` + `POST /api/attempts` sẵn có:
  - `active.examId === exam.id` (bài cũ **chưa** hết giờ) → hỏi "Tiếp tục bài cũ" / "Bỏ, làm lại" (làm lại = gọi `/abandon` rồi start mới).
  - `active == null` (không có, **hoặc** bài cũ đã hết giờ → BE đã auto-abandon → `/active` 404) → start mới luôn. **Trường hợp hết giờ là vô hình**, không cần branch riêng.
  - `active` cho exam khác → switch-exam dialog (flow sẵn có, ATTEMPT_LIFECYCLE §6.3).

## 5. Những điều FE KHÔNG cần lo

- Không có endpoint mới nào cho session/duration — tất cả là hành vi nội bộ của endpoint sẵn có.
- BE không gửi "còn 5 phút" — FE tự tính nếu muốn cảnh báo sớm.
- Không cần phát hiện "bài cũ hết giờ" riêng — `GET /active` 404 đã gói trọn.

---

> Mọi field/endpoint cụ thể khác: **Swagger** là nguồn chính xác.
