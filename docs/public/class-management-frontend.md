---
feature: class-management
status: approved
type: frontend
last-updated: 2026-06-06
author: Backend team
depends-on: [admin-users, teacher-grading, attempt-lifecycle]
---

# Class Management — Lưu ý cho Frontend

> Doc này **chỉ ghi điểm FE cần biết + ý nghĩa data**. **Chi tiết request/response API
> xem Swagger.** BE giải thích ý nghĩa; render là việc của FE.
>
> Bối cảnh: [Class-management design](class-management-design.md) ·
> [Test cases](class-management-testcases.md).

---

## TL;DR cho FE

(1) **Admin** quản lý lớp (tạo/sửa/xóa + gán teacher/student). (2) **Teacher** chọn lớp mình dạy
→ xem **chi tiết lớp** (roster + tiến độ HS) + danh sách bài chờ chấm → chấm. (3) **Student** xem
**lớp của mình** (giáo viên + bạn học). **Lớp đang chọn = `{id}` trong path** (không phải query
`?classId=`); FE tự nhớ — BE không lưu "lớp đang chọn".

> **v2 (mới):** HS **có thể thuộc nhiều lớp** cùng lúc; teacher có endpoint **chi tiết lớp**;
> student có **`/api/me/classes`**. *(Đã triển khai lên dev; chi tiết shape xem Swagger.)*

---

## 1. Role journey overview

| Role | Làm gì với feature này |
|------|------------------------|
| **Admin** | Quản lý lớp: tạo/sửa/list/chi tiết; thêm/gỡ teacher; thêm/gỡ student (**HS có thể vào nhiều lớp** — v2); xóa lớp (chỉ khi đã rỗng). |
| **Teacher** | Chọn lớp mình dạy → **xem chi tiết lớp** (`GET /api/teacher/classes/{id}`: roster + tiến độ HS + co-teachers) + danh sách bài chờ chấm → vào chấm (teacher-grading). |
| **Student** *(v2)* | Xem lớp của mình: `GET /api/me/classes` (+ detail) → **giáo viên** (tên + email) + **bạn học** (chỉ tên). |
| **Parent** | Không tương tác trực tiếp với feature này. |

## 2. Data semantics — vì sao có / dùng làm gì

| Data / hành vi | Ý nghĩa | Hệ quả cho FE |
|----------------|---------|----------------|
| **Lớp đang chọn (`{id}` trong path)** | Lớp teacher đang chọn để lọc danh sách bài — truyền qua **path** `GET /api/teacher/classes/{id}/submissions`, **không** phải query `?classId=`. **BE không lưu** "lớp hiện tại". | FE tự nhớ lựa chọn (state/localStorage); đổi lớp = gọi lại với `{id}` khác. |
| **`GET /api/teacher/classes`** | Danh sách lớp **teacher đang dạy** (admin: tất cả); mỗi lớp có `studentCount` + **`pendingGradingCount`** (số bài chờ chấm). | Dropdown "chọn lớp" có thể hiện badge số bài chờ chấm cạnh tên lớp (demo gọn). Teacher không dạy lớp nào → list rỗng. |
| **submissions item** | Một bài đã nộp của HS trong lớp: `{attemptId, student, exam, submittedAt, isFullyGraded, score,...}`. Loại **abandoned**. | `attemptId` để mở chấm. `isFullyGraded=false` = còn chờ chấm tay. |
| **`?status=pending\|all`** | `all` (default) = mọi bài đã nộp; `pending` = chưa chấm xong (`is_fully_graded=false`). | FE cho teacher chuyển tab "Chờ chấm / Tất cả". |
| **403 ở submissions/grade** | Teacher **không dạy lớp** đó (hoặc không chung lớp với HS). | Không phải bug — ẩn lớp không thuộc quyền; nếu gặp 403 thì refresh "lớp tôi dạy". |
| **Quyền chấm** | Teacher chấm được bài của HS **nếu dạy lớp của HS** (admin chấm hết). `classId` chỉ lọc danh sách, không phải biên quyền. | FE chỉ cần đảm bảo mở chấm từ list hợp lệ; BE tự kiểm tra lại khi chấm. |
| **Xóa lớp** | Chỉ xóa được khi **0 teacher & 0 student**. | FE: disable nút "Xóa" khi còn thành viên; hoặc bắt lỗi 400 `Class has N members; remove all first` → hướng dẫn admin kick hết trước. |
| **Thêm HS vào lớp** *(v2 đổi)* | **HS được vào NHIỀU lớp** cùng lúc. Chỉ chặn thêm **trùng cùng** lớp (409). | Cho phép gán 1 HS cho nhiều lớp. Không còn lỗi "đã thuộc lớp khác". Muốn rời 1 lớp thì DELETE lớp đó (độc lập). |
| **Teacher class-detail** *(v2)* `GET /api/teacher/classes/{id}` | Chi tiết lớp: `teachers[]` (id, fullName) + `students[]` (id, fullName, email + **tiến độ**: `submittedCount`, `averagePercentage`, `pendingGradingCount`, `lastSubmittedAt`). Auth: teacher phải dạy lớp → 403; admin xem mọi lớp. | Dựng trang "lớp của tôi": roster + cột tiến độ. `averagePercentage` có thể **null** (HS chưa có bài đã chấm xong). Tiến độ là **toàn cục theo HS** (mọi đề), không phải riêng lớp. |
| **Student my-classes** *(v2)* `GET /api/me/classes` (+ `/{id}`) | List lớp em đang học; detail có `teachers[]` (id, fullName, **email**) + `classmates[]` (id, fullName — **không email**, đã loại chính em). | `/{id}` mà em **không phải thành viên** → **404** (không lộ lớp tồn tại). |

## 3. Luồng admin — quản lý lớp

- **Gán teacher/student**: lấy danh sách user qua `GET /api/admin/users?role=teacher|student` (sẵn có) → chọn → POST vào lớp. BE validate role (gán nhầm role → 400).
- **HS đa lớp** *(v2)*: 1 HS gán được vào nhiều lớp; chỉ chặn thêm trùng **cùng** lớp (409). Không còn lỗi "đã thuộc lớp khác". Muốn HS rời 1 lớp → DELETE lớp đó (các lớp khác không ảnh hưởng).
- **Xóa lớp an toàn**: trang nên hiện rõ số teacher/student; nút Xóa **chỉ bật khi cả hai = 0**. Đây là cơ chế "tránh xóa nhầm" — admin phải kick hết trước.

## 4. Luồng teacher — chọn lớp & chấm

1. Vào trang chấm → gọi `GET /api/teacher/classes` → dropdown chọn lớp (nhớ lựa chọn).
2. Chọn lớp X → `GET /api/teacher/classes/{X}/submissions?status=pending` → danh sách bài chờ chấm.
3. Chọn 1 bài → mở detail/chấm (luồng teacher-grading; BE tự kiểm tra quyền theo lớp khi chấm).
4. Đổi sang lớp Y → gọi lại `GET /api/teacher/classes/Y/submissions` (lớp ở path). Không cần báo BE "đổi lớp".

## 5. Những điều FE KHÔNG cần lo

- Không có endpoint "set active class" — `classId` thuần là filter do FE quản lý.
- Không cần tự kiểm tra teacher có dạy lớp/HS không — BE enforce ở submissions/class-detail (403) và lúc chấm (403); student-view tự scope theo `me`.
- Tiến độ HS (teacher class-detail) là **toàn cục** — không cần FE tự tính per-lớp.

## 6. Error scenarios

| Status | Detail (chứa) | Khi nào | Xử lý |
|--------|---------------|---------|-------|
| 400 | `Class has N members; remove all first` | Xóa lớp còn thành viên | Disable nút xóa khi còn member; hoặc báo + hướng dẫn kick. |
| 400 | role mismatch (teacher/student) | Gán sai role vào lớp | Lọc đúng role ở dropdown. |
| 403 | (scoping) | Teacher xem/chấm/class-detail lớp không thuộc quyền | Refresh "lớp tôi dạy"; ẩn lớp ngoài quyền. |
| 404 | (various) | Lớp/user không tồn tại; **hoặc student xem lớp mình không thuộc** (`/me/classes/{id}`) | Refresh danh sách; student chỉ mở lớp từ `/me/classes`. |
| 409 | already a member | Gán user đã ở **chính lớp đó** (teacher hoặc student) | Bỏ qua/ báo nhẹ — đã là thành viên. |

> *(v2: bỏ lỗi 400 "Student already in class X; remove first" — HS giờ vào nhiều lớp được.)*

---

> Mọi field/endpoint cụ thể khác: **Swagger** là nguồn chính xác.
