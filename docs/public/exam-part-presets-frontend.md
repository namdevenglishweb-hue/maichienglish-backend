---
feature: exam-part-presets
type: frontend
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: 2026-06-13 (rev3) — B3–B7 SHIPPED; status markers + API contract
  synced to implementation (scaffold-section, exams/scaffold, error-codes,
  validate-on-save, eligibility_reason, full preset catalogue + aiGenSupported/
  imageDependent). B8 + multi-core remain ⏳.
---

# Part Presets — Lưu ý cho Frontend

> **Triết lý:** doc giải thích *ý nghĩa data + luồng + ý đồ*; schema cụ thể xem
> **Swagger**. Render/component là quyết định của FE.

## ⚠️ Bảng trạng thái
| Ký hiệu | Nghĩa |
|---|---|
| ✅ **ĐÃ SHIP** | API/field có thật trên dev, gọi được ngay. |
| ⏳ **ĐỂ SAU** | AI-gen đa-core / cả-đề / render ảnh — amendment đợt sau. |

**Một câu:** "Preset" = khuôn cứng của một Part Cambridge (số câu, số option, độ
dài, CEFR…). Dùng ở **2 nơi**: (1) **Builder** — tạo section/đề đúng khuôn để đổ
nội dung (✅ scaffold + validate-on-save đã ship cho MỌI Part); (2) **AI
generate** — sinh nội dung theo khuôn, đè đề gốc (✅ cho Part MC; các dạng khác ⏳).

---

## 1. Role journey
- **Admin** — builder (tạo đề/section theo khuôn) + AI-gen 1 part theo khuôn (gen vẫn admin-only).
- **Teacher** — đọc khuôn + scaffold/builder (các endpoint scaffold auth **teacher/admin**).
- **Student / Parent** — không đổi.

---

## 2. Luồng & API (✅ tất cả đã ship)

### 2.1 Builder "Thêm phần thi" theo khuôn
- Dropdown 2 cấp **KET/PET → kỹ năng → Part** (hiển thị `label` + `labelVi`),
  + option cuối **"Tùy chỉnh (không theo chuẩn)"** = flow tự do. Nguồn: **GET /api/presets** ✅.
- Chọn 1 Part ⇒ lấy **template section rỗng đúng khuôn** từ:
  **`POST /api/presets/scaffold-section { partCode }`** ✅ (auth teacher/admin) →
  trả `{ section: <template> }`. **Template KHÔNG được persist** — FE đưa nó vào
  exam đang dựng rồi lưu (xem 2.2/2.5). Template gồm: `type`, `part_label`,
  `instructions`, `max_audio_plays`, `part_code`, `materials[]` (placeholder, có
  `{{gap:N}}` nếu cloze; media `url:"pending://replace"`+`meta.pendingReplacement`),
  `questions[]` (placeholder: options A/B/…, `correct_index:0`, fill_blank
  `correct_answers:["?"]`, writing/speaking `prompt`). Sai/thiếu `partCode` → 400.
- Section theo khuôn: badge **"Chuẩn Cambridge · <label>"** + **khóa** nút phá cấu
  trúc (thêm/xóa câu, thêm/xóa option, đổi loại câu) + nút **"Chuyển sang tùy
  chỉnh"**. BE đã sẵn sàng: `part_code` **persist trên section** (✅) và
  **validate-on-save chặn cấu trúc sai** (✅, xem 2.4).

### 2.2 Tạo nhanh "khung chuẩn Cambridge" (cả đề)
- Checkbox "Tạo sẵn khung chuẩn Cambridge 2020" →
  **`POST /api/exams/scaffold { level, skill, formatStandard?, title? }`** ✅
  (auth teacher/admin) → tạo exam **unpublished** + đủ section theo thứ tự, mỗi
  section scaffold từ preset; lưu `formatStandard` + `part_code` từng section.
  Trả `ExamResponse` + `createdCounts`. Gọi `GET /api/exams/{id}?include=sections`
  để lấy id.
  - **`skill` chỉ `reading` | `listening`** (Writing/Speaking là Part lẻ, không
    phải bài thi độc lập) → sai → 400. (vd KET reading ⇒ 5 part P1–P5.)

### 2.3 AI generate một part theo khuôn
- Dialog gen **một part** (Mode 2): gửi **`partCode`** trong
  `POST /api/admin/exam-generations/section` ✅. Sai mã → 400; bỏ trống = hành vi cũ.
- **Chỉ Part `aiGenSupported=true` mới AI-gen được** (đợt này: core
  `multiple_choice` ⇒ `KET_R_P3`, `PET_R_P3`). Gửi `partCode` của Part chưa hỗ
  trợ (vd matching/cloze/listening) → **400** "chưa hỗ trợ AI-gen". Builder/scaffold
  thì dùng được mọi Part.
- Đề sinh theo **cấu trúc khuôn, đè đề gốc** (xem §4). Provenance trong
  `report.sections[]`: `part_code`, `mode`, `core`, `eligibility_reason` ✅.

### 2.4 Validate-on-save (✅ đã ship — B5)
- Lưu section **có `part_code` kèm câu hỏi đầy đủ** mà cấu trúc lệch khuôn →
  **HARD-BLOCK 400**, message kèm **field error codes** (xem §5). Áp ở:
  `POST /api/exams` (nested), `POST /api/exams/scaffold`, và
  `POST /api/exams/{examId}/sections` khi gửi kèm `questions`.
- Section **không** `part_code` → không kiểm gì thêm (hành vi cũ y nguyên).
- Section có `part_code` nhưng **chưa có câu** (tạo nháp granular) → chỉ kiểm
  `part_code` là mã hợp lệ (cấu trúc kiểm sau, khi đủ câu) — giống policy gap-marker.

### 2.5 Lưu đề/section
- `partCode` đã thêm vào `SectionCreate` + `SectionUpdate` ✅ → FE gửi kèm khi tạo/
  sửa section; BE persist + validate (2.4). Khi lưu cả đề dùng `POST /api/exams`
  (nested, mỗi section mang `partCode`).

---

## 3. GET /api/presets — fields (✅ đã ship, FULL catalogue)
Authenticated. Trả `{ "presets": [ … ] }` — **đủ Part KET/PET** (Reading/Listening/
Writing/Speaking). Mỗi item:
`partCode, level, skill, defaultPosition, label, labelVi, sectionType,
questionType, numQuestions, optionsPerQuestion` (null cho fill_blank/writing/
speaking)`, wordCountRange` ([min,max] hoặc null)`, cefrLevel, pointsPerQuestion,
gapMarkers, sharedOptions, materialsSpec` ([{type,count,note}])`, instructionsEn,
aiCore` (tên core dự kiến hoặc null)`, aiGenSupported` (true nếu AI-gen được ngay)`,
imageDependent` (true nếu Part cần ảnh — picture-MC / story / mô tả ảnh / visual)`.
**FE không hardcode** khuôn — luôn đọc từ API.

## 4. Data semantics — ý nghĩa cốt lõi
- **PRESET LÀ TRÊN HẾT** (AI-gen): có `partCode` ⇒ số câu/option/độ dài/CEFR theo
  KHUÔN, **không** theo đề gốc. Vd đề gốc 6 câu/4 option + `KET_R_P3` (5/3) ⇒ ra
  **5 câu/3 option**. Đề gốc chỉ là "nguồn kỹ năng/văn phong".
- `report.sections[].mode` = `"spec"` | `"rewrite"`; `core` = core đã dùng;
  **`eligibility_reason`** = lý do (vd `"spec — eligible (core: multiple_choice)"`
  hoặc `"rewrite — k=2 < 3"`) → FE hiển thị "Part này chạy rewrite vì: …". Chỉ
  có ở job chạy v3.
- Khuôn là **hằng số format Cambridge** — cache thoải mái, đổi theo deploy BE.

## 5. Mã lỗi validator + GET /api/presets/error-codes (✅ đã ship — B7)
- **`GET /api/presets/error-codes`** (authenticated) → `{ errorCodes: [{ code,
  field, messageEn, messageVi }] }`. **FE map theo `code`** (field chỉ là path-hint
  template, message thật đã kèm cả EN/VI).
- Codes hiện có: `PRESET_SECTION_TYPE`, `PRESET_NUM_QUESTIONS`,
  `PRESET_QUESTION_TYPE`, `PRESET_OPTIONS`. (Không còn `PRESET_MATERIALS` — bỏ ở
  B5 vì materials đa dạng theo Part.)

## 6. Để sau (⏳ — CHƯA có, đừng gọi)
- **AI-gen đa-core**: cloze / matching / gapped_text / listening (các Part
  `aiGenSupported=false`). Builder/scaffold/validate đã chạy cho chúng; chỉ AI-gen chưa.
- **AI-gen cả đề theo khuôn** (Mode-1 / preview nhận `partCode` từng section) — B8.
- **render_hint** (khung mô phỏng văn bản Part 1) — đọc `materialsSpec[].note`.
- **Listening audio**: section có material `meta.pendingReplacement=true` (scaffold
  đặt sẵn) → FE badge "Chưa có audio" + chặn publish; transcript ẩn với học sinh.
  (Cơ chế pendingReplacement đã có; UI là việc FE.)

## 7. Slugs doc
Design: `exam-part-presets-design` · Frontend: `exam-part-presets-frontend`
(qua `GET /api/docs` + `GET /api/docs/{slug}`)
