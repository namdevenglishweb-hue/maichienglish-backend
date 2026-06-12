---
feature: attempt-highlights
status: approved
type: frontend
last-updated: 2026-06-07
author: Backend team
depends-on: [attempt-lifecycle]
---

# Attempt Highlights — Lưu ý cho Frontend

> Doc này **ghi ý nghĩa data + contract offset FE cần để interop**. Cách render cụ thể (HTML/CSS,
> màu, icon) là **việc của FE**. Chi tiết request/response xem **Swagger**.
>
> Bối cảnh: [Design](attempt-highlights-design.md) · [Test cases](attempt-highlights-testcases.md).

---

## TL;DR cho FE

HS **bôi đen** 1 đoạn chữ bất kỳ **hiển thị cho mình** trong attempt → tạo 1 highlight, kèm **note tùy chọn**.
1 highlight = **`(targetKey, rangeStart, rangeEnd)`** trên **chuỗi nguồn** của 1 text-run + `quotedText` (snapshot) + `note?`/`color?`.
**BE coi `targetKey` opaque** — FE tự map `targetKey ↔ element` và tự tính offset. Cho **chồng nhau**.
Tạo/sửa/xóa **chỉ khi đang làm bài** (`in_progress`); sau nộp **read-only** (vẫn hiện). **Giáo viên xem được** (kèm note) khi review.

---

## 1. Role journey

| Role | Làm gì |
|------|--------|
| **Student** (owner) | Bôi đoạn chữ → tạo highlight (+ note tùy chọn); sửa note/màu; xóa. Chỉ khi `in_progress`. |
| **Teacher / Admin / Parent** | Khi review attempt: **thấy highlight + note** của HS (read-only). Teacher bị scope theo lớp như xem detail. |

## 2. `targetKey` — định danh text-run (FE sở hữu)

BE không hiểu `targetKey`, chỉ lưu/trả. FE đặt + map theo grammar:
```
section:{sectionId}:instructions
material:{sectionId}:{idx}:content   (hoặc :label / :alt)
question:{questionId}:stem
question:{questionId}:option:{i}
question:{questionId}:prompt          (writing/speaking đề bài)
question:{questionId}:label|prefix|postfix
answer:{questionId}                    (bài viết HS)
```
→ Mỗi text-run FE render gắn 1 `targetKey` ổn định; highlight tham chiếu qua đó.

## 3. Contract OFFSET (bắt buộc đúng, nếu sai sẽ lệch)

- `rangeStart/rangeEnd` tính trên **chuỗi NGUỒN** của text-run (vd `material.content` GỐC còn `{{gap:N}}`), **KHÔNG** trên DOM đã render.
- Lấy `quotedText = source.substring(rangeStart, rangeEnd)` — **KHÔNG** dùng `selection.toString()` (chuỗi DOM đã thay gap → lệch).
- Container hiển thị giữ text 1:1: `white-space: pre-wrap`, giữ `\n`, **không** transform (smart-quote/markdown/trim). *(Cùng kỷ luật offset như writing-comment §4.)*
- FE giữ map: mỗi đoạn đã render ↔ `[start,end)` nguồn → khi user bôi, map anchor/focus của `getSelection()` về offset nguồn qua map này.
- ⭐ **Cảnh báo `answer:{questionId}` (bài viết HS):** đây là text **đang sửa được** (khác passage/đề bất biến). Nếu HS highlight bài viết **rồi gõ sửa tiếp**, offset sẽ lệch. FE nên: so `quotedText` với answer hiện tại → khớp thì render, **không khớp thì tự dò lại vị trí `quotedText` hoặc bỏ render** highlight đó (đừng render sai chỗ). Các targetKey trên nội dung đề thì không lo (bất biến).

## 4. Câu hỏi FE — xóa highlight

- Xóa = `DELETE /api/attempts/{id}/highlights/{hlId}` → 204. **Đơn vị xóa = nguyên 1 highlight theo id** (không xóa sub-range ở v1).
- **Overlap:** một đoạn render có thể bị **nhiều** highlight phủ. FE nên cho mỗi đoạn biết **danh sách id phủ nó**; click vào vệt:
  - 1 id phủ → xóa luôn.
  - nhiều id → hiện menu nhỏ (theo `quotedText`) để chọn, hoặc xóa cái trên cùng.
- Sau xóa: **chạy lại segmentation** trên đúng text-run (các highlight chồng còn lại vẫn giữ) — không reload trang.
- Id không tồn tại/không phải của mình → 404 → đọc lại `highlights[]` qua detail/resume (không có GET list riêng).

## 5. Câu hỏi FE — selection chạy qua `{{gap:N}}`

- **Cho phép.** Offset trên chuỗi nguồn nên 1 vệt phủ qua gap chỉ là `[start,end)` bao gồm cả ký tự marker `{{gap:N}}`.
- **Quy tắc DUY NHẤT phải enforce:** **không để biên highlight rơi vào GIỮA token `{{gap:N}}`** — nếu endpoint map vào trong khoảng marker thì **snap về mép token** (đầu/cuối). Tránh làm hỏng việc thay gap khi render lại.
- Render passage = 1 lần segmentation chung: mốc biên = **vị trí gap ∪ start/end highlight** → mỗi đoạn render thành `text` / `<mark>` / `<input>`(gap). Vệt highlight "ôm" quanh ô input một cách tự nhiên. (Tô nền ô input hay không = tuỳ FE.)
- `quotedText` chứa marker gốc `{{gap:N}}`; muốn hiển thị đẹp thì FE thay bằng "[…]" lúc show, nhưng **lưu nguyên** offset/quote.

## 6. Render gợi ý (FE sở hữu)

Map data → render trực tiếp, không parse lại:
1. Đọc `highlights[]` **trong payload resume/detail** (không có endpoint GET list riêng) → gom theo `targetKey`.
2. Mỗi text-run: **boundary-sweep** — gom mọi mốc start/end, cắt thành đoạn liên tiếp, đoạn nào ≥1 highlight phủ → bọc `<mark>` (nhiều phủ → đậm/đa màu). O(k log k), k nhỏ.
3. Highlight có `note` → gắn affordance (icon/chấm ở mép, hover/click xem-sửa note). Cách thể hiện là việc FE.

> ⚠️ **Nguồn-text khác nhau giữa "đang làm" và "review":**
> - **Đang làm bài** (start/resume): payload có **đủ** exam tree (instructions + passage `content` + questions) → render in-context **mọi** highlight.
> - **Review** (GET `/attempts/{id}`): detail chỉ có `answers[]` (stem/options/prompt + bài viết HS) + `highlights[]`, **KHÔNG** có instructions/passage. Nên:
>   - Highlight `question:*` / `answer:*` → vẫn render in-context (nguồn ở `answers[]`).
>   - Highlight `section:*:instructions` / `material:*` (passage) → **không có chuỗi nguồn** → hiển thị **danh sách snippet** (`quotedText` + `note`), HOẶC tự gọi `GET /api/exams/{id}?include=sections` lấy passage rồi overlay. *(BE v1 không nhồi materials vào detail để giữ payload gọn.)*

## 7. Data semantics

| Data | Ý nghĩa | Hệ quả FE |
|------|---------|-----------|
| `highlights[]` (trong resume + detail) | Danh sách highlight của attempt, mỗi item `{id, targetKey, rangeStart, rangeEnd, quotedText, note, color, createdAt}` | Load 1 lần khi mở/resume; áp lại bằng segmentation. |
| `note` | Ghi chú tùy chọn của HS cho highlight đó | ⚠️ **Không riêng tư** — GV/admin/parent thấy khi review. Nên cho HS biết. |
| `in_progress` (trạng thái attempt) | Chỉ khi này mới tạo/sửa/xóa được | Sau nộp: ẩn các nút tạo/sửa/xóa; chỉ hiển thị. |

## 8. Error scenarios

| Status | Khi nào | Xử lý |
|--------|---------|-------|
| 403 | Không phải owner; hoặc mutation khi attempt đã nộp/abandon | Ẩn action khi không phải chủ / sau nộp. |
| 400 | Tạo/sửa/xóa khi attempt không `in_progress` | Không expose action lúc đó. |
| 404 | highlight/attempt không tồn tại / không thuộc mình | Đọc lại `highlights[]` qua detail/resume. |
| 422 | payload sai (`rangeEnd<=rangeStart`, thiếu `targetKey`/`quotedText`, note/quote quá dài) | Pre-validate: end>start, có targetKey + quotedText, cap độ dài. |

---

> Field/endpoint cụ thể: **Swagger** là nguồn chính xác.
