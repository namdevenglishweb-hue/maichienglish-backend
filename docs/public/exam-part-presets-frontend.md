---
feature: exam-part-presets
type: frontend
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: DRAFT 2026-06-13 (rev2) — phủ ĐẦY ĐỦ tầm nhìn FE của amendment
  Part-Presets (builder dropdown, badge+khóa cấu trúc, scaffold section/đề,
  eligibility surface, validator inline) + ĐÁNH STATUS từng API (đã ship / sắp
  làm / để sau) để FE không gọi nhầm API chưa tồn tại
---

# Part Presets — Lưu ý cho Frontend (bản đầy đủ)

> **Triết lý:** doc giải thích *ý nghĩa data + luồng + ý đồ*; schema cụ thể xem
> **Swagger**. Render/component là quyết định của FE.

## ⚠️ ĐỌC TRƯỚC — Bảng trạng thái (đừng gọi API chưa có)

| Ký hiệu | Nghĩa |
|---|---|
| ✅ **ĐÃ SHIP** | Đã merge (commit `d7f3877`), gọi được ngay. |
| 🔨 **BACKEND SẮP LÀM** | API/field CHƯA tồn tại — đang chờ chốt thứ tự build. **Đừng code FE gọi nó tới khi BE báo xong.** |
| ⏳ **ĐỂ SAU** | Multi-core / listening / ảnh — amendment đợt sau. |

**Một câu:** "Preset" = khuôn cứng của một Part Cambridge (số câu, số option,
độ dài, CEFR). Dùng ở **2 nơi**: (1) **Builder** — giáo viên tạo section/đề
đúng khuôn để đổ nội dung (🔨 phần lớn chờ BE); (2) **AI generate** — sinh nội
dung theo khuôn, đè đề gốc (✅ đã ship cho MC).

---

## 1. Role journey

- **Admin** — (a) Builder: tạo đề/section theo khuôn chuẩn; (b) AI-gen: sinh 1
  part theo khuôn. (Gen vẫn admin-only.)
- **Teacher** — đọc danh sách khuôn (GET /api/presets ✅); builder theo khuôn
  khi BE mở (🔨).
- **Student / Parent** — không đổi.

---

## 2. Luồng & API theo trạng thái

### 2.1 Builder "Thêm phần thi" theo khuôn
- Dropdown 2 cấp **KET/PET → kỹ năng → Part** (hiển thị `label` + `labelVi`),
  + option cuối **"Tùy chỉnh (không theo chuẩn)"** = flow tự do hiện tại.
  - Nguồn dữ liệu dropdown: **GET /api/presets** ✅ (đã có; xem §3).
- Chọn 1 Part ⇒ server **scaffold** section rỗng đúng khung (part_label,
  instructions, type, N câu rỗng đúng question_type + số option, material
  placeholder) để FE render và đổ nội dung.
  - Endpoint: **POST /api/sections nhận `part_code`** → 🔨 **CHƯA CÓ** (work-item B3).
- Section theo khuôn: badge **"Chuẩn Cambridge · <label>"** + **khóa** nút phá
  cấu trúc (thêm/xóa câu, thêm/xóa option, đổi loại câu) + nút **"Chuyển sang
  tùy chỉnh"** (confirm → bỏ khuôn).
  - Cần section **nhớ** được khuôn (`part_code` lưu trên section) → 🔨 **CHƯA CÓ**
    (work-item B2: migration). Tới khi có, FE chưa hiển thị badge bền vững được.

### 2.2 Tạo nhanh "khung chuẩn Cambridge"
- Dialog "Tạo đề thi mới": checkbox **"Tạo sẵn khung chuẩn Cambridge 2020"**
  (mặc định bật khi level KET/PET) → tạo đề + đủ section theo thứ tự (PET Reading
  = P1→P6…), `is_published=false`.
  - Endpoint: **POST /api/exams/scaffold** `{level, skill, format_standard}` →
    🔨 **CHƯA CÓ** (work-item B4).

### 2.3 AI generate một part theo khuôn
- Trong dialog gen **một part** (Mode 2), cho chọn 1 khuôn → gửi **`partCode`**
  trong `POST /api/admin/exam-generations/section`.
  - ✅ **ĐÃ SHIP.** `partCode` optional; sai mã → 400; bỏ trống = hành vi cũ.
- Đề sinh ra theo **cấu trúc khuôn** (số câu/option/độ dài/CEFR), **đè đề gốc**
  (xem §4). Provenance `report.sections[].part_code` ✅.
- Hiển thị **core + eligibility** ("Part này chạy spec/rewrite vì …") khi gen:
  - Field `{mode, core, eligibility_reason}` per section → `mode` đã có trong
    report; **`core`/`eligibility_reason` 🔨 CHƯA surface** (work-item B6).
- Nhãn **thang K**: giữ theo v3 (xem doc `exam-gen-v3-spec-mode-frontend`).
  Không đổi gì mới ở đợt preset.

### 2.4 Lỗi validator inline
- Khi save section theo khuôn (builder) hoặc nhận output gen, BE trả lỗi
  **theo field + mã code** để FE map ra tiếng Việt inline (xem §5).
  - Ở **AI-gen** lỗi khuôn được BE tự xử trong retry (FE thấy qua `reason`) ✅.
  - Ở **builder-save** chạy validator → trả field errors: 🔨 **CHƯA CÓ**
    (work-item B5; gắn validator vào POST/PUT section).

---

## 3. GET /api/presets — fields & ngữ nghĩa (✅ đã ship)

Authenticated (admin/teacher). Trả `{ "presets": [ … ] }`. Mỗi item (✅ có sẵn):
`partCode` (id để gửi lại), `level`, `skill`, `label`, `labelVi`,
`sectionType`, `questionType`, `numQuestions`, `optionsPerQuestion`,
`wordCountRange [min,max]`, `cefrLevel`, `pointsPerQuestion`.

> **Dữ liệu đợt này:** chỉ **2 khuôn MC** (`KET_R_P3`, `PET_R_P3`). 🔨 Để builder
> phủ đủ **22 Part** (Reading/Listening/Writing/Speaking) cần BE bổ sung dữ liệu
> preset (work-item B1) + có thể thêm field cho builder: `materialsSpec`,
> `gapMarkers`, `sharedOptions`, `instructionsEn`, `defaultPosition` (🔨). FE
> **không hardcode** khuôn — luôn đọc từ API; field mới sẽ xuất hiện khi BE thêm.

## 4. Data semantics — ý nghĩa cốt lõi

- **PRESET LÀ TRÊN HẾT.** Khi gen có `partCode`: số câu / option / độ dài / CEFR
  của đề **theo KHUÔN, KHÔNG theo đề gốc**. Ví dụ đề gốc 6 câu/4 option + khuôn
  `KET_R_P3` (5 câu/3 option) ⇒ **đề ra 5 câu/3 option**. Đề gốc giờ chỉ là
  "nguồn kỹ năng/văn phong"; khung là chuẩn Cambridge. (UX: cho admin chọn khuôn
  đúng level; lệch nhiều vẫn ra theo khuôn — đúng thiết kế.)
- Chỉ áp khi part đi **spec mode** (v3, K≥3, MC đủ điều kiện). Part rơi rewrite
  ⇒ `partCode` bị bỏ qua; `report.mode` cho biết đường nào. (`core`/
  `eligibility_reason` sẽ rõ hơn khi B6 xong.)
- Khuôn là **hằng số format Cambridge** (không phải data admin sửa) — cache thoải
  mái, đổi theo deploy BE.

## 5. Bảng mã lỗi validator (để FE map inline)

Mã do `preset_validator` sinh (✅ tồn tại trong BE; ✅ dùng ở AI-gen, 🔨 sẽ gắn
vào builder-save ở B5). FE map mã → thông báo VN cạnh đúng field:

| code | field | nghĩa |
|---|---|---|
| `PRESET_NUM_QUESTIONS` | questions | Sai số câu so với khuôn |
| `PRESET_OPTIONS` | questions[i].options | Sai số lựa chọn 1 câu |
| `PRESET_QUESTION_TYPE` | questions[i].question_type | Sai loại câu |
| `PRESET_SECTION_TYPE` | type | Sai loại section |
| `PRESET_MATERIALS` | materials | Sai số/loại material (MC = 1 text) |

> 🔨 Cân nhắc 1 endpoint liệt kê code→message mặc định (B7) nếu FE muốn nguồn
> tập trung thay vì map cứng. Hiện cứ map theo bảng trên.

## 6. Để sau (⏳ — KHÔNG làm đợt này)
- **render_hint** khung mô phỏng văn bản (handwritten_note / metal_sign /
  phone_message / email / notice) cho short_texts Part 1.
- **Render option dài** (Part 4 gapped) + **bảng matching A–H** + hiển thị "3
  option không dùng".
- **Listening:** badge "Chưa có audio" (`meta.pendingReplacement`) + chặn
  publish; hiện transcript cho admin/teacher, ẩn với học sinh.
- AI-gen multi-core (cloze/matching/gapped/listening) + AI-gen preset cho Mode-1
  cả-đề.

## 7. Slugs doc
- Design: `exam-part-presets-design` · Frontend: `exam-part-presets-frontend`
(qua `GET /api/docs` + `GET /api/docs/{slug}`)
