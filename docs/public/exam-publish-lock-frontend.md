---
feature: exam-publish-lock
status: implemented
type: frontend
last-updated: 2026-06-10
author: Backend team
depends-on: [exam, attempt-lifecycle]
---

# Exam Publish Lock — Lưu ý cho Frontend

> Doc này ghi **ý nghĩa hành vi + lỗi FE cần xử lý**. Endpoint/field cụ thể xem **Swagger**.
> Đây là feature **phía admin authoring** (soạn đề) — không ảnh hưởng học sinh/giáo viên chấm.
>
> Bối cảnh: [Design](exam-publish-lock-design.md).

---

## TL;DR cho FE (admin authoring)

> **CẬP NHẬT 2026-06-10:** khoá nội dung theo **ATTEMPT**, không theo publish.

- Đề **đã có ≥1 attempt** ⇒ **không sửa được NỘI DUNG** (section / question / materials) → BE trả **409** — bất kể publish hay chưa.
- Đề **chưa có attempt** ⇒ sửa nội dung **tự do** (publish hay chưa đều được, không cần unpublish trước).
- **Metadata luôn sửa** được (title / level / skill / duration / description).
- **Unpublish luôn cho phép** (kể cả khi có attempt). Muốn đổi nội dung đề đã có attempt → **tạo đề mới**.

## 1. Role journey (admin)

| Trạng thái đề | Admin làm được gì |
|---|---|
| **Chưa có attempt** (draft hoặc published) | Sửa thoải mái: metadata + section + question + materials. Publish/unpublish tuỳ ý. |
| **Đã có attempt** | Sửa **metadata** OK; **unpublish** OK. **Nội dung đông cứng** (sửa section/question/materials → 409). Muốn đổi nội dung → **tạo đề mới**. |

## 2. Data semantics — vì sao có hành vi này

| Hành vi | Ý nghĩa | Hệ quả FE |
|---|---|---|
| **Nội dung khoá khi có attempt** | Đề đã có HS làm bài → đổi nội dung làm sai điểm/lệch highlight bài cũ. | Khi đề **có attempt**: **disable** nút sửa/thêm/xóa section-question; nếu vẫn gọi và nhận 409 → báo "Đề đã có người làm, không sửa nội dung được; tạo đề mới". ⚠️ **BE v1 KHÔNG trả cờ `hasAttempts`** trong payload exam → FE **dựa vào 409** (proactively disable phải tự đếm; thêm cờ là việc v2). |
| **Metadata + unpublish không khoá** | Sửa tên/level/thời lượng + gỡ publish không đổi nội dung bài/anchor. | Luôn cho sửa metadata + unpublish, kể cả khi có attempt. |
| **Publish / delete vẫn được** | Publish, hoặc xóa (mềm) đề — không phải "sửa nội dung". | Giữ các nút này. |

> **BE giải thích ý nghĩa, FE quyết hiển thị.** Doc không ép cách render — chỉ nêu các trạng thái + lỗi để FE dựng luồng soạn đề hợp lý.

## 3. Error scenarios

| Status | Detail (chứa) | Khi nào | Xử lý gợi ý |
|--------|---------------|---------|-------------|
| **409** | `Exam already has attempts; its content can no longer be edited` | Sửa/thêm/xóa section/question/materials của đề **đã có attempt** | Giải thích đề đã có người làm → không sửa nội dung; gợi ý tạo đề mới. (Hoặc disable sẵn UI khi có attempt.) |
| 200/OK | — | Sửa **metadata** / **unpublish** đề có attempt | Cho phép bình thường. |

## 4. Những điều FE KHÔNG cần lo

- Không cần FE tự kiểm "đề có publish/có attempt chưa" để chặn — **BE enforce** ở mọi đường sửa nội dung (chặn ở tầng service nên mọi caller đều dính). FE chỉ cần **xử lý 409 cho mượt** + (tùy chọn) disable UI trước để UX tốt.
- Không cần xử lý gì cho học sinh/giáo viên — feature chỉ chạm luồng soạn đề của admin.

---

> Endpoint/field cụ thể: **Swagger** là nguồn chính xác.
