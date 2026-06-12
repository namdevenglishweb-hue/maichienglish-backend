---
feature: writing-speaking
type: frontend
status: approved
last-updated: 2026-06-02
author: Backend team
depends-on: [attempts, questions, media-upload]
---

# Writing & Speaking — Lưu ý cho Frontend khi integrate

> Doc này **chỉ ghi các điểm FE cần biết** + **ý nghĩa data** BE trả về. **Chi tiết
> request/response API xem Swagger.** Render thế nào là quyết định của FE — BE chỉ
> giải thích ý nghĩa để FE hiểu rõ.
>
> Scope: phase *cấu trúc đề + student trả lời*, **dừng ở submit**. Phase post-submit
> (teacher chấm, comment, badge, lock) là **feature riêng** → xem
> `docs/teacher-grading/` (frontend notes của feature đó).
>
> Bối cảnh thiết kế: [Writing-speaking design](writing-speaking-design.md).
> Test cases: [Writing-speaking test cases](writing-speaking-testcases.md).

---

## TL;DR cho FE

Hai loại câu hỏi mới `writing` (gõ text) và `speaking` (**upload file** audio/video).
Cả hai **không auto-grade** lúc submit. **v1 = upload-only** — học sinh chọn file có
sẵn để tải lên, **chưa quay/ghi trực tiếp trên trình duyệt** (đó là v2). Phần FE cần
nắm: luồng upload + validate trước khi tải, và quy tắc `mediaType`.

---

## 1. Role journey overview

| Role | Làm gì với feature này (tới submit) |
|------|--------------------------------------|
| **Student** | Trong lúc làm bài: gõ text (writing) hoặc **upload file** audio/video có sẵn (speaking) → lưu qua `PATCH /answers`. Submit → thấy điểm tạm (chỉ phần auto-graded) + cờ `isFullyGraded=false` nếu đề có writing/speaking. |
| **Admin** | Tạo đề: set `question_type = writing`/`speaking`, nhập `prompt` (+ option như `minWords`, `promptAudioUrl`…). |
| **Teacher / Parent** | **Không** dính ở phase này — họ vào cuộc sau submit (feature teacher-grading). |

Mấu chốt: ở phase này chỉ **student** và **admin** thao tác. Mọi thứ sau khi nộp →
feature khác.

## 2. Data semantics — vì sao có data này & dùng làm gì

### 2.1 Phân biệt loại đề

| Data | Ý nghĩa & vì sao tồn tại | Lưu ý cho FE |
|------|---------------------------|---------------|
| `question.questionType` | **Nguồn sự thật** để biết câu là `writing`/`speaking`/MC/… BE trả đúng loại để FE biết dựng trải nghiệm phù hợp. | Phân nhánh theo field này, KHÔNG dựa `section.type`. |
| `section.type` | Chỉ là **rendering hint cấp section** (vd "section này phần writing"). BE không ép nội dung khớp type. | Dùng để gợi ý bố cục; per-câu vẫn theo `questionType`. |

### 2.2 Writing — `question_data`

| Field | Ý nghĩa | Lưu ý |
|-------|---------|-------|
| `prompt` | Đề bài. **Bắt buộc.** | — |
| `minWords` / `maxWords` | **Gợi ý số từ**, KHÔNG phải ràng buộc. BE không enforce (vì marker thật chấp nhận lệch). | FE có thể đếm từ & cảnh báo, nhưng **đừng block** save/submit. |
| `exampleAnswer` | Đáp án mẫu cho admin/teacher tham khảo. | **Không bao giờ** xuất hiện trong response gửi student (BE đã strip) — FE không cần xử lý. |

`student_answer` writing = `{ "text": string }`. Empty string được chấp nhận (lưu nháp).

### 2.3 Speaking — `question_data`

| Field | Ý nghĩa | Lưu ý |
|-------|---------|-------|
| `prompt` | Đề bài. **Bắt buộc.** | — |
| `promptAudioUrl` | Audio examiner đọc đề (optional). **Không** bị strip — student cần nghe. | — |
| `exampleAnswerAudioUrl` | Bản ghi mẫu, admin-only. | BE strip trước khi trả student — FE không cần xử lý. |

> `maxDurationSeconds` / `prepTimeSeconds` (đếm ngược, tự dừng) chỉ phục vụ **quay
> trực tiếp** → không có ở v1, dời sang v2.

### 2.4 Speaking — `student_answer`

| Field | Ý nghĩa | Lưu ý |
|-------|---------|-------|
| `recordingUrl` | Public URL sau khi upload xong. | Lưu vào `student_answer` qua `PATCH /answers`. |
| `mediaType` | `"audio"`/`"video"` — **FE chỉ định** theo loại file student upload. BE lưu nguyên, dùng để downstream biết phát `<audio>` hay `<video>`. | Suy từ `contentType` của file: `audio/*` → `"audio"`, `video/*` → `"video"`. |
| `durationSeconds` | Optional. BE không validate. | Upload-only thường không có sẵn → **có thể bỏ qua**; field vắng cũng không sao. |

### 2.5 `isFullyGraded` (trả ở submit / attempt view / history)

- Ý nghĩa: `false` = đã nộp nhưng **còn câu writing/speaking chưa được teacher chấm**; điểm hiện tại chỉ là phần auto-graded. `true` = không còn gì phải chấm tay.
- Hệ quả cho FE: phân biệt trạng thái "Đang chấm" vs "Đã chấm" (cách hiển thị tùy FE). Chi tiết badge UX ở feature teacher-grading.

## 3. Luồng upload speaking (v1 = upload-only)

Student **chọn file có sẵn** để tải lên — không quay trực tiếp. Flow logic (không phải code bắt buộc):

1. Student chọn file audio/video (file picker).
2. **Pre-validate phía FE**: `contentType` nằm trong whitelist ([§7](#7-mime--size--giá-trị-cụ-thể)) + size ≤ cap → báo lỗi sớm trước khi tải.
3. Xin signed URL: `POST /attempts/{id}/speaking-upload` với `{questionId, filename, contentType, fileSizeBytes}`.
4. PUT binary file lên `uploadUrl`.
5. HEAD verify `publicUrl` (cùng pattern MEDIA_UPLOAD).
6. Lưu `recordingUrl` (= `publicUrl`) + `mediaType` (suy từ `contentType`) qua `PATCH /answers`.

> **v2 sẽ thêm** đường quay trực tiếp trong browser (MediaRecorder). Khi đó FE phải
> lo thêm: feature-detect MIME (Safari không hỗ trợ webm), chờ event `stop` trước khi
> gom Blob, giải phóng mic/cam. **v1 không cần các thứ này** vì file đã có sẵn.

## 4. Re-upload, orphan, đổi media type

- Trước submit, student upload lại bao nhiêu lần tùy ý; mỗi lần = upload mới = URL mới.
- Có thể đổi audio↔video giữa các lần (chọn file khác loại).
- Save ghi đè `recordingUrl` + `mediaType`. File cũ thành orphan — **không phải việc của FE**, BE dọn sau.
- Sau submit: không cho upload/save nữa (hành vi sẵn có).

## 5. Strip rules — FE thấy gì / không thấy gì

| Field | Khi nào bị strip |
|-------|-------------------|
| `exampleAnswer` (writing) | Luôn strip cho student/parent. Admin & teacher thấy. |
| `exampleAnswerAudioUrl` (speaking) | Như trên. |
| `promptAudioUrl` (speaking) | **Không** strip — student cần. |
| `isCorrect` trên writing/speaking | `null` cho tới khi teacher chấm. |
| `pointsEarned` trên writing/speaking | `0` cho tới khi teacher chấm. |

FE không cần làm gì cho việc strip — BE đã lọc sẵn.

## 6. Lưu ý bảo mật — URL recording

- Recording nằm ở bucket **public** `student_recordings`. URL theo UUID (không
  enumerable) nhưng **ai có URL đều fetch được file**.
- Hệ quả FE: đừng làm tính năng copy-link/share URL recording; đừng để URL lọt vào
  screenshot/log. **Video chứa mặt student** — cẩn trọng hơn. Coi URL là bán-nhạy-cảm.

## 7. MIME & size — giá trị cụ thể

- Allowed MIME: `audio/webm`, `audio/mp4`, `audio/mpeg`, `audio/wav`, `video/webm`, `video/mp4`. `video/quicktime` (.mov) **không** nhận — app chỉ dùng trên **laptop/PC** (không hỗ trợ điện thoại) nên file .mov gốc iPhone gần như không xảy ra, và .mov/HEVC phát lại không ổn định trên Chrome/Firefox.
- Size cap: **50 MB** (Supabase Free tier; sẽ lên 100 MB khi paid plan — FE chỉ cần enforce theo spec mới nhất).
- FE nên pre-validate MIME + size **trước** khi upload để báo lỗi sớm.

## 8. Edge cases nghiệp vụ FE cần nắm

- Student upload speaking thành công nhưng **không** `PATCH /answers` → coi như chưa trả lời; submit không có gì để chấm.
- Mỗi câu speaking nhận **cả** audio lẫn video ở v1; admin hướng dẫn loại mong muốn qua `prompt`.
- Section/câu writing/speaking thường 1 câu nhưng schema cho phép N → FE nên render generic (loop `section.questions`).

## 9. Error scenarios (chỉ phase cấu trúc/upload)

> Lỗi grading + comment thuộc feature teacher-grading.

| Status | Detail (chứa) | Khi nào | Gợi ý xử lý |
|--------|---------------|---------|--------------|
| 400 | `Attempt is not active` | Upload speaking sau khi attempt đã submit/abandon | Refresh state; ẩn nút upload khi đó. |
| 400 | `Question is not a speaking question` | Gọi speaking-upload cho câu writing/MC | Bug FE — check `questionType` trước khi hiện UI upload. |
| 403 | `Not the owner of this attempt` | Student khác cố upload | Bug auth. |
| 404 | (various) | Question/attempt không tồn tại | Refresh state, retry. |
| 422 | (Pydantic) | Payload sai — `contentType` ngoài whitelist, `fileSizeBytes` quá cap (50 MB), … | Pre-validate size + MIME trước upload. |

---

> Mọi field/endpoint cụ thể khác: **Swagger** là nguồn chính xác.
