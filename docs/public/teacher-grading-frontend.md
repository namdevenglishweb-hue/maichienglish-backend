---
feature: teacher-grading
type: frontend
status: approved
last-updated: 2026-06-02
author: Backend team
depends-on: [writing-speaking, attempts]
---

# Teacher Grading & Comments — Lưu ý cho Frontend

> Doc này **chỉ ghi điểm FE cần biết + ý nghĩa data**. **Chi tiết request/response API
> xem Swagger.** Render thế nào là việc của FE — BE chỉ giải thích ý nghĩa.
>
> Scope: phase *post-submit* (chấm, comment, badge, lock, visibility). Phase *trước
> submit* (render đề, ghi âm, upload) → `docs/writing-speaking/` (frontend notes).
>
> Bối cảnh: [Teacher-grading design](teacher-grading-design.md) ·
> [Test cases](teacher-grading-testcases.md).

---

## TL;DR cho FE

Sau khi student nộp bài writing/speaking, **teacher/admin** chấm điểm + (tùy chọn)
nhận xét. **Điểm và nhận xét là 2 việc độc lập** (2 nhóm endpoint riêng). Phần FE dễ
sai nhất là **bắt offset cho writing comment** — có ràng buộc CSS bắt buộc (xem [§4](#4-writing-comment--ràng-buộc-offset-quan-trọng)).

---

## 1. Role journey overview

> ⚠️ **Scoped theo lớp**: teacher chỉ chấm/xem bài của **HS thuộc lớp mình dạy** (per
> class-management). Luồng thực tế: teacher **chọn lớp** → xem danh sách bài của lớp
> (`docs/class-management/`) → mở 1 bài → chấm. Admin toàn quyền.

| Role | Làm gì với feature này |
|------|------------------------|
| **Teacher** | Chọn lớp mình dạy → mở bài đã nộp **của HS trong lớp** → cho điểm từng câu writing/speaking; (tùy chọn) bôi đen text writing để nhận xét, hoặc viết 1 nhận xét tổng cho speaking. Chấm dần nhiều lần được. Bài của HS **ngoài lớp** → 403. |
| **Admin** | Y hệt teacher nhưng **không bị scope** — chấm/xem mọi bài. |
| **Student** (owner) | Không chấm. Xem điểm + nhận xét; **thấy nhận xét ngay khi teacher thêm**, kể cả khi bài chưa chấm xong. *(Email báo khi chấm xong — xem ghi chú E5 ở §10.)* |
| **Parent** (linked) | Như student: chỉ xem (điểm + nhận xét của con). |

## 2. Vòng đời post-submit + `isFullyGraded`

`isFullyGraded` là **khái niệm chia sẻ** giữa 2 feature (writing-speaking set, teacher-grading lật). Xuất hiện trên submit response, attempt view, history, và response chấm.

| Giá trị | Ý nghĩa | Hệ quả FE |
|---------|---------|-----------|
| `false` | Đề có câu writing/speaking & teacher chưa chấm hết. Điểm hiện tại chỉ là phần auto-graded; writing/speaking có `pointsEarned:0, isCorrect:null`. | Hiện trạng thái "đang chấm" (cách hiển thị tùy FE). |
| `true` | Đề không có writing/speaking (lật ngay lúc submit) **hoặc** teacher đã chấm hết. Điểm chốt. | Hiện trạng thái "đã chấm". |

Abandon: luôn `true` (không chấm gì); không có email E5.

## 3. Chấm điểm — ý nghĩa data

> **Câu nào cần chấm?** Trong `answers[]` của attempt detail, lọc câu có
> **`questionType ∈ {writing, speaking}`** *và* **`isCorrect === null`** (chưa chấm).
> MC/fill/matching đã auto-grade (có `isCorrect` true/false) → không cần chấm tay.
> **Thang điểm:** chấm bằng **điểm tuyệt đối** (số nguyên) `0 .. answer.points` —
> `answer.points` = điểm tối đa admin set cho câu, min = 0. **KHÔNG chấm theo %**; điểm
> tổng + % cả bài do BE tự tính từ tổng points.

| Data / hành vi | Ý nghĩa | Lưu ý FE |
|----------------|---------|----------|
| `questionType` | `writing`/`speaking` = cần chấm tay; MC/fill/matching = auto-graded. | Lọc câu cần chấm theo field này. |
| `isCorrect = null` (sau submit) | Câu writing/speaking **chưa được chấm**. | = "cần chấm". Sau khi chấm → `true` + có `pointsEarned`. |
| `answer.points` | Điểm **tối đa** của câu (admin set khi tạo đề). | Dùng làm `max` cho input điểm; `min` = 0. |
| `pointsEarned` | Điểm teacher cho 1 câu (số nguyên). **Cả writing lẫn speaking đều chấm bằng con số này.** | Ràng buộc `0 ≤ pointsEarned ≤ answer.points`. Pre-validate ở input; BE trả 422 nếu ngoài range. |
| Chấm dần (partial) | Gửi subset câu bất kỳ; lần sau chấm tiếp; câu đã chấm giữ nguyên. | `isFullyGraded` chỉ lật `true` khi **mọi** câu writing/speaking đã chấm. |
| Response chấm | Trả `graded` (số câu chấm lần này) + điểm recompute + `isFullyGraded`. | `isFullyGraded:true` ⇒ lần này vừa lật cờ ⇒ có thể báo "đã hoàn tất chấm bài". |
| Điểm vs nhận xét | **Độc lập.** Teacher có thể chỉ cho điểm, hoặc nhận xét trước rồi cho điểm sau. | Cờ fully-graded **chỉ phụ thuộc điểm**, không phụ thuộc nhận xét. |

## 4. Writing comment — ràng buộc offset (QUAN TRỌNG)

Nhận xét writing gắn vào **đoạn bôi đen**, định vị bằng char offset `[rangeStart, rangeEnd)`
(giống `String.substring`). BE lưu kèm `quotedText` và **kiểm tra nghiêm** `quotedText`
phải bằng đúng `studentText.substring(rangeStart, rangeEnd)`. Vì vậy:

- ⚠️ **Container chứa text writing PHẢI giữ whitespace 1:1 với chuỗi gốc BE lưu.** Nếu không, offset lệch và mọi POST comment fail 400 `quotedText does not match`. Cụ thể:
  1. **`white-space: pre-wrap`** (hoặc `<pre>`). Mặc định `normal` gộp khoảng trắng & bỏ `\n` → đổi cả phạm vi user select được.
  2. **Render text như một (chuỗi) Text node giữ nguyên `\n`** — ĐỪNG tách `\n` thành nhiều `<p>`/`<div>` (mất ký tự `\n` khỏi DOM → lệch 1 offset/dòng). Tách nhiều Text node để highlight thì OK *miễn không bỏ ký tự nào*.
  3. **Không transform** — không smart-quote, không markdown, không auto-link, không trim.
- ⚠️ **Luôn lấy `quotedText` từ `text.substring(rangeStart, rangeEnd)`**, KHÔNG từ `selection.toString()` (cái này trả text *đã render*, có thể gộp whitespace → lệch).
- Overlap: BE từ chối 2 range đè nhau (chỉ cần chung 1 ký tự) → 400 `overlaps existing comment`. Chạm nhau (`e1==s2`) thì OK. FE nên báo toast + clear selection.
- Sửa comment: **chỉ** sửa được `commentText`; đổi range thì DELETE rồi POST. Bất kỳ teacher/admin sửa được (không chỉ người tạo).

## 5. Speaking comment — ý nghĩa

- **1 nhận xét tổng thể duy nhất/đáp án** (free text). Teacher tự ghi timestamp trong text (vd `0:34 phát âm...`) — **server không parse** timestamp.
- PUT = UPSERT (ghi hoặc đè). DELETE = xóa. Không có lịch sử sửa (`SpeakingCommentView` không có `updatedAt`).

## 6. Comment xuất hiện ở đâu trong response

| questionType | Field trên answer | Khi chưa có |
|--------------|-------------------|-------------|
| `writing` | `writingComments: WritingCommentView[]` | `[]` |
| `speaking` | `speakingComment: SpeakingCommentView \| null` | `null` |
| MC/fill/matching | (không có 2 field này) | — FE key theo questionType |

## 7. Visibility

- Student/parent **thấy nhận xét ngay khi teacher thêm** — không đợi `isFullyGraded`. Refresh trong lúc "đang chấm" là thấy feedback dần.
- Student/parent chỉ **xem**, không chấm/không sửa nhận xét.
- **Teacher**: chấm + thêm/sửa/xóa nhận xét — **chỉ cho HS thuộc lớp mình dạy** (per class-management); HS ngoài lớp → 403. **Admin**: không bị scope, thao tác mọi bài. (Tới khi bài khóa.)

## 8. Lock — khi nào đóng băng

Khi `isFullyGraded` lật `false→true`, **cả điểm lẫn nhận xét khóa cùng lúc**:
- Sửa điểm → 400 `already fully graded`.
- Sửa nhận xét → 400 `comments locked`.
- GET vẫn chạy — data read-only.

→ FE nên disable control chấm/nhận xét (hoặc hiện banner) khi xem bài đã khóa. Re-grade là v2.

## 9. Error scenarios

| Status | Detail (chứa) | Khi nào | Gợi ý xử lý |
|--------|---------------|---------|--------------|
| 400 | `overlaps existing comment` | Range writing đè comment khác | Toast "đoạn này đã có nhận xét…"; clear selection. |
| 400 | `quotedText does not match` | `quotedText` sai so với range | Bug bắt offset — luôn lấy từ `text.substring`; check CSS container (§4). |
| 400 | `Range exceeds text length` | `rangeEnd > text.length` | Bug bắt offset. |
| 400 | `is for writing/speaking answers only` | Gọi comment sai loại answer | Check `questionType` trước khi hiện UI nhận xét. |
| 400 | `already fully graded` | Sửa điểm sau khóa | Disable UI chấm khi bài đã khóa. |
| 400 | `comments locked` | Sửa nhận xét sau khóa | Disable UI nhận xét khi bài đã khóa. |
| 400 | `not submitted` / `Attempt is abandoned` | Chấm/nhận xét bài chưa nộp | Không nên xảy ra — UI không expose action lúc đó. |
| 403 | (scoping) | Teacher chấm/xem bài của HS **ngoài lớp mình dạy** (per class-management) | Chỉ mở chấm từ danh sách bài của lớp mình; nếu gặp → refresh "lớp tôi dạy". |
| 403 | (various) | Caller không phải teacher/admin | Bug auth/role. |
| 404 | (various) | Comment/question/attempt không tồn tại | Refresh state, retry. |
| 422 | (Pydantic) | Payload sai — `commentText` rỗng, `rangeStart` âm, `pointsEarned` ngoài range… | Pre-validate hợp lý. |

## 10. FAQ

- **Student thấy nhận xét trước khi chấm xong?** Có. Refresh là thấy nhận xét teacher đã thêm; cờ vẫn `false` đến câu cuối.
- **Teacher A sửa comment của teacher B?** Được. `updatedAt` bump, `createdBy` giữ là tác giả gốc.
- **Hiện tên giáo viên trên nhận xét (để in)?** `GET /api/attempts/{id}` trả kèm `createdByName` (full_name của teacher) cạnh `createdBy` (uuid) trong mỗi `writingComments[]` và `speakingComment` — dùng `createdByName` để in/hiển thị. Có thể `null` nếu tài khoản teacher đã bị xóa (`createdBy` SET NULL). *(Các endpoint POST/PUT comment hiện vẫn chỉ trả `createdBy`; tên lấy từ GET detail.)*
- **Nhận xét mà không cho điểm?** Được — 2 endpoint độc lập. Cờ chỉ phụ thuộc điểm.
- **Exam 0 câu writing/speaking?** `isFullyGraded=true` ngay lúc submit; không cần teacher; không E5.
- **Khi nào học sinh nhận email E5?** ⚠️ **Hiện CHƯA gửi** — email feature chưa được build (mới ở mức design), nên khi chấm xong BE chỉ log, không có email rời hệ thống. FE đừng hứa với học sinh là "sẽ nhận email". Khi email feature ra: gửi 1 lần khi cờ lật `false→true`, gửi nền (học sinh có thể thấy điểm trên UI trước khi email tới).

---

> Mọi field/endpoint cụ thể khác: **Swagger** là nguồn chính xác.
