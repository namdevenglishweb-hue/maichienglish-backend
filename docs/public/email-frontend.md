---
feature: email
type: frontend
status: approved
last-updated: 2026-06-02
author: Backend team
depends-on: [auth, admin-users, attempts]
---

# Email — Lưu ý cho Frontend khi integrate

> Doc này **chỉ ghi các điểm FE cần biết** khi tích hợp với Backend cho feature
> email. **Chi tiết request/response của API xem trên Swagger** — không lặp lại ở đây.
>
> Bối cảnh thiết kế: [Email design](email-design.md). Test cases: [Email test cases](email-testcases.md).

---

## TL;DR cho FE

Phần lớn feature email là **vô hình với FE** (gửi nền sau khi BE đã trả response).
Chỉ có **2 điểm thực sự đụng FE**: hành vi của `devCode` trong password-reset, và
nút "Gửi lại mã" cần cool-down. Các phần còn lại chỉ là toast tùy chọn.

> **Lưu ý đọc doc:** BE giải thích *ý nghĩa* của data/hành vi để FE hiểu; còn
> hiển thị/render thế nào là quyết định của FE.

---

## Role journey overview

Với feature email, các role **không thao tác trực tiếp** với email — email là
*side-effect* của hành động họ vốn đã làm. Bảng dưới cho biết mỗi role "chạm" vào
feature ở đâu:

| Role | Khi nào dính tới email | FE thấy gì / cần làm gì |
|------|------------------------|--------------------------|
| **Student** | (1) Quên mật khẩu → nhận OTP. (2) Được admin tạo account → nhận welcome. (3) Nộp bài → *không nhận gì* (email đi cho phụ huynh). | Màn hình quên-mật-khẩu (nhập email → nhập OTP). Submit attempt: không UI gì liên quan email. |
| **Parent** | Con nộp bài thành công → nhận email thông báo. | **Không có UI nào** trong app — phụ huynh nhận ở hộp thư. Email có link tới trang xem chi tiết attempt (yêu cầu parent đăng nhập). |
| **Teacher** | **v1: không có.** (Thông báo cho teacher là v2 — xem [design §14](email-design.md#14-future-v2-teacher--grading-notifications).) | — |
| **Admin** | Tạo user mới → hệ thống tự gửi welcome email cho user đó. | Có thể (tùy chọn) hiện toast "Đã gửi email chào mừng". `tempPassword` vẫn trả về để giao tay nếu cần. |

Điểm mấu chốt FE cần nắm: **không có "trang quản lý email" nào**; mọi email phát
sinh tự động từ 3 hành động sẵn có (quên mật khẩu, admin tạo user, nộp bài).

## Data semantics — vì sao có data này & dùng làm gì

Các trường/hành vi BE phơi ra cho FE và *ý nghĩa* của chúng (Swagger chỉ cho biết
kiểu dữ liệu, không cho biết ý đồ):

| Data / hành vi | Ý nghĩa & vì sao tồn tại | Hệ quả cho FE |
|----------------|---------------------------|----------------|
| **Silent 200** ở request-code | BE cố tình **luôn trả 200** dù email có tồn tại hay không, để chống dò email (anti-enumeration). | Đừng dùng response để suy ra email hợp lệ. Luôn điều hướng sang màn nhập OTP với cùng một thông báo trung tính. |
| `devCode` (conditional field) | Là OTP cleartext, **chỉ tồn tại khi BE chạy `EMAIL_PROVIDER=noop`** (môi trường dev). Sinh ra để dev/test không cần email thật. Prod **không bao giờ** có field này. | Coi là optional (`devCode?`). Có thể dùng auto-fill OTP ở môi trường dev; **không** được giả định nó tồn tại ở prod. |
| `tempPassword` (admin create user) | Mật khẩu tạm cleartext của user mới. Vẫn trả về **dù** welcome email đã gửi — làm phương án dự phòng để admin giao tay khi email fail. | Admin UI có thể hiện/copy mật khẩu này. Không phải lỗi khi vừa có email vừa có field này. |
| OTP **TTL 10 phút** | Mã hết hạn sau 10 phút (quy ước nghiệp vụ, không phải lỗi kỹ thuật). | Khi verify gặp lỗi hết hạn, FE nên cho phép xin mã mới (xem cool-down bên dưới). |
| Rate limit **5 OTP / giờ / email** | Chống spam + bảo vệ quota Gmail. Vượt ngưỡng BE *vẫn trả 200 nhưng không gửi mail* (giữ anti-enumeration). | User sẽ không thấy lỗi mà cũng không nhận mã → bắt buộc có cool-down phía client (xem §2). |

---

## 1. Password reset

- Luồng UI **không đổi** so với spec hiện tại: gọi request-code → BE trả **silent 200** ("If the email exists, a reset code has been sent") → hiển thị màn nhập OTP. BE không tiết lộ email có tồn tại hay không (anti-enumeration), nên **đừng** dựa vào response để phán đoán email hợp lệ.
- **`devCode` không còn xuất hiện trong response ở production.** Trước đây dev tự động điền OTP nhờ field này; giờ field đó **chỉ có khi BE chạy `EMAIL_PROVIDER=noop`** (local/dev). FE phải xử lý được trường hợp `devCode` vắng mặt — coi nó là `devCode?: string` optional.
- Nếu FE từng phụ thuộc `devCode` ở bất kỳ luồng prod nào → đó là bug cần fix khi rollout.
- OTP có **TTL 10 phút**; quá hạn → BE trả lỗi khi verify (xem mã lỗi trên Swagger). FE nên hiển thị thông báo hết hạn và cho phép xin mã mới.

## 2. Resend OTP — cool-down phía client (BẮT BUỘC)

- BE giới hạn **5 OTP request / giờ / email**. Vượt ngưỡng BE **vẫn trả 200** nhưng **không gửi email** (anti-enumeration), nên user sẽ không thấy lỗi mà cũng không nhận được mã.
- → FE **phải disable nút "Gửi lại mã" 60 giây** sau mỗi lần bấm. Không có cool-down, user sốt ruột bấm liên tục và đốt sạch hạn mức rất nhanh mà không hề biết.

## 3. Admin tạo user (welcome email)

- **Response shape không đổi** — `tempPassword` vẫn trả về để admin giao tay nếu email fail.
- Welcome email gửi nền tự động tới user mới. FE admin **có thể** (tùy chọn) hiện toast "Đã gửi email chào mừng tới {email}" khi tạo thành công. Không bắt buộc.

## 4. Submit attempt (parent notification)

- **Không có thay đổi nào cho FE.** Email gửi tới phụ huynh chạy nền, hoàn toàn vô hình với student.
- Lưu ý nghiệp vụ: chỉ `submit` **thành công** mới gửi mail cho phụ huynh. `abandon` **không** gửi. (FE không cần làm gì, chỉ để nắm hành vi.)

## 5. Những điều FE KHÔNG cần lo

- Không cần biết SMTP, App Password, hay provider — toàn bộ server-side.
- Không có endpoint mới nào FE phải gọi chỉ để "gửi email"; email là side-effect của các API sẵn có (request-code, admin create user, submit).
- Email gửi fail **không bao giờ** khiến API trả non-2xx → FE không cần handle lỗi riêng cho việc gửi mail.

---

## 6. TypeScript types liên quan

```ts
// Schema hiện có — devCode thành optional / vắng mặt ở prod
interface PasswordResetCodeResponseData {
  message: string;
  devCode?: string;   // CHỈ có khi BE chạy EMAIL_PROVIDER=noop
}

// Không có thay đổi shape nào khác hướng tới FE — phần còn lại của
// feature là vô hình (gửi email nền).
```

> Mọi field/endpoint cụ thể khác: tra **Swagger** là nguồn chính xác.
