---
feature: exam-part-presets
type: frontend
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: DRAFT 2026-06-13 — GET /api/presets (authenticated); field optional
  partCode trên POST .../section; preset đè cấu trúc đề gốc; provenance
  report.part_code; phạm vi Mode-2 đợt này
---

# Part Presets (MC-only) — Lưu ý cho Frontend

> **Triết lý doc này:** giải thích *ý nghĩa data & ý đồ* để FE hiểu; schema
> request/response cụ thể xem **Swagger**. Render/hiển thị là quyết định của FE.
>
> **Tin quan trọng nhất:** chỉ có **1 endpoint mới** (`GET /api/presets`) và
> **1 field optional mới** (`partCode`) trên endpoint gen-một-part đã có. Mọi
> thứ khác là nội dung trong `report` (jsonb tự do).

---

## 1. Role journey

- **Admin** — luồng gen y như cũ (admin-only). Điểm mới duy nhất: trong dialog
  gen **một part** (Mode 2), admin có thể chọn một **Khuôn (Part preset)**; khi
  chọn, đề sinh ra theo đúng cấu trúc khuôn (số câu/số option/độ dài/CEFR),
  **không theo đề gốc**.
- **Teacher** — đọc được danh sách khuôn qua `GET /api/presets` (authenticated)
  để hiển thị/đối chiếu; chưa có builder theo preset trong đợt này.
- **Student / Parent** — không thấy gì khác (đề ra vẫn là draft chờ review).

```
Dialog gen-part: admin chọn nguồn (section gốc) + K + (tùy chọn) Khuôn partCode
  + (tùy chọn) model/promptVersion=v3
  → có partCode  ⇒ cấu trúc theo PRESET (đè đề gốc) — vd KET_R_P3 = 5 câu/3 option
  → không partCode ⇒ hành vi cũ (cấu trúc theo đề gốc)
  → poll job → review → publish (y như cũ)
```

## 2. Cái gì mới cho FE

**(a) `GET /api/presets`** (authenticated — admin/teacher đều gọi được). Trả
danh sách khuôn để dựng dropdown. Mỗi item (xem Swagger cho field đầy đủ) mang:
`partCode` (id để gửi lại khi gen), `level` (KET/PET), `skill`, `label`
("Part 3"), `labelVi` (mô tả ngắn tiếng Việt cho dropdown), `numQuestions`,
`optionsPerQuestion`, `wordCountRange`, `cefrLevel`. Đợt này có 2 khuôn:
`KET_R_P3`, `PET_R_P3`. **FE KHÔNG hardcode khuôn — luôn đọc từ API này.**

**(b) `partCode` (optional) trên `POST /api/admin/exam-generations/section`.**
Gửi `partCode` lấy từ (a) để bind part vào khuôn. Bỏ trống = hành vi cũ. Sai mã
khuôn → **400** ngay (không tạo job).

**(c) `report.sections[].part_code`** — echo lại khuôn đã dùng (provenance).
Chỉ xuất hiện khi gen có gửi `partCode`. FE có thể hiển thị "đã sinh theo khuôn
PET Reading Part 3".

## 3. Data semantics — ý nghĩa quan trọng

- **PRESET LÀ TRÊN HẾT.** Khi có `partCode`, **số câu / số option / độ dài /
  CEFR của đề sinh ra theo KHUÔN, không theo đề gốc** — kể cả khi đề gốc có số
  câu/option khác. Ví dụ: đề gốc 6 câu 4 option + khuôn `KET_R_P3` (5 câu / 3
  option) ⇒ **đề ra 5 câu / 3 option**. Ý đồ: đề gốc giờ chỉ là "nguồn kỹ năng/
  văn phong" để AI học cách hỏi, còn *khung* là chuẩn Cambridge.
  → UX gợi ý: cho admin chọn khuôn **đúng level** của nguồn; nếu khuôn lệch
  nhiều với nguồn, đề ra vẫn theo khuôn (đúng thiết kế, không phải bug).
- `report.mode === "spec"` + các field provenance (`topic`, `diversity_seed`,
  `trigram_overlap_pct`…) vẫn như tài liệu v3 — preset **không** đổi ý nghĩa
  chúng. `part_code` là field provenance mới, cùng nhóm.
- Khuôn là **hằng số format Cambridge** (không phải dữ liệu admin sửa được) —
  FE coi như danh mục tĩnh, cache thoải mái (đổi theo deploy backend).

## 4. Lưu ý integrate khác

- **Phạm vi đợt này = gen MỘT PART (Mode 2 `/section`).** Gen cả-đề (Mode 1)
  và preview cả-đề **chưa** nhận `partCode` (mỗi section cần khuôn riêng — sẽ
  thêm khi có builder). FE đừng gửi `partCode` cho `POST /api/admin/exam-
  generations` (cả-đề) / `/preview` đợt này.
- Chỉ áp khi đi **spec mode** (promptVersion v3, K≥3, section đủ điều kiện MC).
  Nếu part rơi về rewrite (K≤2 / không đủ điều kiện), `partCode` bị bỏ qua —
  `report.mode` cho biết đường nào đã đi.
- **Không** có endpoint/field nào bị xóa hay đổi nghĩa — backward-compatible.
- (Hạ tầng, không ảnh hưởng FE) backend thêm timeout 180s/req để tránh treo, và
  default model chuyển sang Opus 4.8 — FE không cần làm gì.

## 5. Slugs doc
- Design: `exam-part-presets-design`
- Frontend: `exam-part-presets-frontend`
(qua `GET /api/docs` + `GET /api/docs/{slug}`)
