---
feature: exam-publish-lock
status: implemented
type: design
last-updated: 2026-06-10
author: Backend team
depends-on: [exam, attempt-lifecycle]
---

# Exam Publish Lock — Khoá sửa nội dung đề khi đã có attempt (Design)

> **CẬP NHẬT 2026-06-10:** khoá nội dung giờ theo **ATTEMPT**, không theo publish. Lý do: thứ
> cần bảo vệ là **toàn vẹn bài HS** (điểm, highlight offset) — chỉ attempt mới đe doạ điều đó,
> publish-state thì không. Bản gốc khoá-theo-publish + chặn-unpublish đã được thay.
>
> **Scope (hiện tại)**:
> - **Nội dung** (section / question / materials): **chặn sửa (409) khi đề đã có ≥1 attempt** —
>   bất kể publish hay chưa.
> - **Metadata** (title/level/skill/duration/description): **luôn cho sửa**.
> - **Unpublish**: **luôn cho phép** (kể cả khi có attempt) — vì toàn vẹn được bảo vệ bằng khoá
>   nội dung, không cần chặn unpublish.
>
> Cùng feature: [Frontend notes](exam-publish-lock-frontend.md) · [test cases](exam-publish-lock-testcases.md).
>
> **Bối cảnh:** nhiều feature (attempt-highlights offset, exam-mode, chấm điểm) **ngầm giả định
> "đề bất biến khi đã có attempt"**. Feature này enforce đúng điều đó.

---

## 1. Hiện trạng (vì sao cần)

Kiểm tra code: **không có khoá nào**.
- [exam_service.update_exam](../../services/exam_service.py) — UPDATE metadata, **không** kiểm `is_published`.
- `section_service.py` + `question_service.py` — **không** có reference `is_published` → admin tạo/sửa/xóa section, question, materials của đề đã publish **tự do**.
- `is_published` chỉ dùng để: chặn thi đề chưa publish, filter list, toggle publish/unpublish/delete.

⇒ Đề đã publish (kể cả đã có HS làm bài) vẫn bị sửa nội dung được → lệch offset highlight, sai điểm bài cũ.

## 2. Overview & sub-features

| # | Sub-feature | Mô tả |
|---|---|---|
| 1 | Khoá sửa nội dung khi **có attempt** | Mọi mutation section/question/materials → **409** nếu đề đã có ≥1 attempt (bất kể publish). |
| 2 | Cho sửa metadata | `PATCH /api/exams/{id}` (title/level/skill/duration/description) **không bị khoá**. |
| 3 | Unpublish luôn cho phép | `unpublish` thành công kể cả khi có attempt (toàn vẹn đã do #1 bảo vệ). |

**Luồng sửa đề:** đề **chưa có attempt** → sửa nội dung tự do (publish hay chưa đều được).
Đề **đã có attempt** ⇒ **đông cứng nội dung** (muốn phiên bản khác → tạo đề mới). Publish-state không còn là điều kiện khoá.

**Không thuộc scope:** chặn sửa metadata; chặn publish; chặn unpublish; chặn delete đề; clone đề.

## 3. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Loại bỏ | Lý do |
|---|-----------|-------------------|---------|-------|
| 1 | Phạm vi khoá | **Chỉ nội dung** (section/question/materials); metadata vẫn sửa | Khoá cả metadata | Title/level… không ảnh hưởng offset/anchor; cho sửa lỗi chính tả tiện hơn. |
| 2 | Cách sửa lại | Đề **chưa có attempt** → sửa tự do (không cần unpublish trước) | Bắt unpublish trước khi sửa | Publish-state không đe doạ toàn vẹn; bỏ bước thừa. |
| 3 | **Trigger khoá nội dung** | **"đề có ≥1 attempt"** (mọi trạng thái) | `is_published` | Thứ cần bảo vệ là bài HS; chỉ attempt mới đe doạ. (Đổi 2026-06-10 từ bản gốc khoá-theo-publish.) |
| 4 | Unpublish | **Luôn cho phép** | Chặn khi có attempt | Toàn vẹn đã do khoá nội dung (#3) lo; chặn unpublish là thừa + bất tiện. |
| 5 | Nơi đặt guard | **Module `services/exam_guards.py`** (chỉ phụ thuộc `exceptions`) | Lặp query ở từng service / để ở exam_service | Tránh import cycle (section/question/exam đều import được); 1 nguồn sự thật. |
| 6 | Mã lỗi | **409 Conflict** | 403 | Xung đột trạng thái tài nguyên (đề đang publish / đã có attempt), không phải thiếu quyền. |
| 7 | Không cần migration | **Pure app-layer guard** | DB trigger | Không đổi schema; logic ở service, dễ test + đổi. |

## 4. Implementation — guard

**`services/exam_guards.py`**:
```python
async def assert_exam_content_editable(conn, exam_id) -> None:
    """409 nếu đề đã có attempt (đông cứng nội dung để bảo vệ bài HS)."""
    has_attempts = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE exam_id=$1)", exam_id,
    )
    if has_attempts:
        raise ConflictError(
            "Exam already has attempts; its content can no longer be edited"
        )

async def assert_no_attempts_among(conn, exam_ids) -> None:
    """Batch variant cho bulk ops: 409 nếu BẤT KỲ exam_id nào trong tập đã có
    attempt. (bulk_* nhận list id, không có exam_id → resolve trước rồi gọi.)"""
    ids = [e for e in (exam_ids or []) if e is not None]
    if not ids:
        return
    has_attempts = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE exam_id = ANY($1::uuid[]))",
        ids,
    )
    if has_attempts:
        raise ConflictError(
            "One or more target items belong to an exam with attempts; "
            "its content can no longer be edited"
        )
```
*(Gọi guard **trong cùng transaction** của mutation, **trước** khi ghi. `unpublish_exam` **không** còn guard nào — luôn cho phép.)*

### 4.1 Wire guard vào 13 hàm mutation (section ×7, question ×6)

| Service | Hàm | Lấy `exam_id` → guard nào |
|---|---|---|
| `section_service` | create_section, create_section_with_questions | **`exam_id` là tham số** → `assert_exam_content_editable` |
| `section_service` | update_section, soft_delete_section, hard_delete_section | fetch `SELECT exam_id FROM sections WHERE id=$section_id` → `assert_exam_content_editable` |
| `section_service` | **bulk_update_sections** (`updates:list[{id,...}]`), **bulk_delete_sections** (`ids:list`) | ⚠️ **không có exam_id** → resolve `SELECT DISTINCT exam_id FROM sections WHERE id = ANY($ids)` → `assert_no_attempts_among` |
| `question_service` | create_question | `section_id` → `SELECT exam_id FROM sections WHERE id=$section_id` → `assert_exam_content_editable` |
| `question_service` | update_question, soft_delete_question, hard_delete_question | `get_exam_id_for_question` (đã có) → `assert_exam_content_editable` |
| `question_service` | **bulk_update_questions** (`updates:list[{id,...}]`), **bulk_delete_questions** (`ids:list`) | ⚠️ **không có exam_id** → resolve `SELECT DISTINCT s.exam_id FROM questions q JOIN sections s ON s.id=q.section_id WHERE q.id = ANY($ids)` → `assert_no_attempts_among` |

### 4.2 Unpublish — KHÔNG guard
- `exam_service.unpublish_exam` luôn set `is_published=false` (kể cả khi có attempt). Guard cũ `assert_exam_has_no_attempts` đã **bỏ**.

### 4.3 Map `ConflictError → 409` — **global handler** (đã chọn khi implement)
- Guard raise `ConflictError`, nhưng các route `sections/questions/exams` **chưa** map nó → sẽ thành 500.
- **Cách làm (đã implement):** thêm **1 global exception handler** ở `main.py`:
  ```python
  @app.exception_handler(ConflictError)
  async def _conflict_handler(request, exc):
      return JSONResponse(status_code=409, content={"detail": str(exc)})
  ```
  → mọi `ConflictError` chưa bắt ở route đều thành **409**. Robust hơn sửa ~10 route lẻ + **tránh rủi ro sót route → 500** (đúng tinh thần risk §8).
- Route nào đã `except ConflictError` cục bộ (vd `attempts`) vẫn chạy handler riêng trước → **không đổi hành vi**.

### 4.4 KHÔNG guard
- `exam_service.update_exam` (metadata) — cho phép.
- `exam_service.publish_exam` — cho phép (đang là điều kiện publish: ≥1 active question).
- `exam_service.delete_exam` — cho phép (soft-delete; giữ nguyên dữ liệu, attempt vẫn tham chiếu).

## 5. API behavior (tóm tắt)

| Endpoint (nhóm) | Đề **0 attempt** | Đề **có attempt** |
|---|---|---|
| Section/Question create/update/delete/bulk | OK (publish hay chưa đều được) | **409** (content frozen) |
| `PATCH /api/exams/{id}` (metadata) | OK | OK |
| `POST /api/exams/{id}/publish` | OK | OK |
| `POST /api/exams/{id}/unpublish` | OK | **OK** (luôn cho phép) |
| `DELETE /api/exams/{id}` | OK | OK (soft) |

## 6. Ripple
- Nhẹ — chỉ thêm guard ở đường authoring. Không đụng thi/chấm/highlight/exam-mode.
- **Củng cố** giả định "đề bất biến khi có attempt" mà attempt-highlights/exam-mode dựa vào → bớt rủi ro lệch offset.

## 7. Appendix — files to change

| File | Change |
|------|--------|
| `services/exam_guards.py` | **2** guard (§4): `assert_exam_content_editable` + batch `assert_no_attempts_among` (đều check attempts) + resolver helpers |
| `services/section_service.py` | guard ở **7** hàm mutation (single + bulk) (§4.1) |
| `services/question_service.py` | guard ở **6** hàm mutation (single + bulk) (§4.1) |
| `services/exam_service.py` | `unpublish_exam` — **bỏ** guard (luôn cho phép) (§4.2) |
| `main.py` | **global `@app.exception_handler(ConflictError)` → 409** (§4.3) |
| `tests/test_exam_publish_lock.py` | test cases (§ testcases doc) |

> Không cần migration (pure app-layer).

## 8. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| Bỏ sót 1 đường mutation → vẫn sửa lén được | Liệt kê đủ 13 hàm (§4.1); test mỗi nhóm; guard ở **service** (không phải route) nên mọi caller đều dính |
| Admin muốn sửa đề đã có attempt | Theo thiết kế: không cho — hướng dẫn clone đề mới (FE giải thích) |
| Race: publish xen giữa lúc đang sửa | Guard chạy trong cùng transaction mutation; publish cũng transaction → tuần tự ở DB |
| Đổi `duration_minutes` (metadata) khi đang có người thi | Cho phép theo quyết định #1; nếu sau muốn chặt hơn (loại duration khỏi metadata cho-sửa) → refine sau |
