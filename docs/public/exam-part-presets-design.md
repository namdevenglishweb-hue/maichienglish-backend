---
feature: exam-part-presets
type: design
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: DRAFT 2026-06-13 (rev2) — phủ ĐẦY ĐỦ amendment Part-Presets (builder
  scaffold, validator save-gate, eligibility surface) ngoài lát cắt AI-gen đã
  ship; đánh STATUS từng phần + bảng BACKEND WORK-ITEMS để chốt thứ tự build.
  MC-only cho AI-gen core; builder/scaffold áp cho MỌI preset về cấu trúc.
---

# Part Presets — Design / Core feature (bản đầy đủ)

> **Một dòng:** PRESET = định nghĩa cứng của một Part Cambridge (số câu/option/
> word-count/CEFR…), một nguồn sự thật dùng chung cho **builder**, **validator**,
> và **AI generate**. AI-gen: preset đè đề gốc về cấu trúc (✅ MC). Builder:
> scaffold section/đề đúng khuôn + khóa cấu trúc (🔨). Multi-core/listening (⏳).
>
> **Nguồn tầm nhìn:** amendment `AMENDMENT_part-presets_multi-core.md` của client
> (§2 preset, §3 builder flow, §4.2 eligibility, §6 listening). Kiến trúc lõi
> v1.1/v1.2/v3 (ANALYZE→leak→generate→shuffle→similarity→blind-solve→FIX→
> all-or-nothing) **GIỮ NGUYÊN — prompt FROZEN.**

## Bảng trạng thái
✅ ĐÃ SHIP (`d7f3877`) · 🔨 BACKEND SẮP LÀM (chờ chốt thứ tự) · ⏳ ĐỂ SAU.

---

## 1. Vấn đề
(a) Builder tự do ⇒ không có gì chặn "Part 1 KET" sai khung. (b) AI-gen lấy cấu
trúc theo đề mẫu nên không ra được Part chuẩn nếu đề mẫu lệch. Gốc chung: **chưa
có định nghĩa máy-đọc-được của từng Part.** → tầng PRESET.

## 2. Preset — một nguồn sự thật (code constant)
`services/presets.py`, `PartPreset` frozen dataclass, KHÔNG DB (format Cambridge,
đổi cùng core/prompt/harness ⇒ thuộc git). ✅ đã có với 2 khuôn MC
(`PET_R_P3` 5q/4opt/B1/220–320; `KET_R_P3` 5q/3opt/A2/150–230).

- 🔨 **B1 — mở rộng đủ 22 Part** (Reading/Listening/Writing/Speaking) cho builder,
  + field builder: `materials_spec`, `gap_markers`, `shared_options`,
  `instructions_en`, `default_position`, `ai_core` (None cho non-MC đợt này).
  Builder/scaffold/validator áp cho **mọi** preset; AI-gen chỉ chạy preset có
  `ai_core="multiple_choice"`.

## 3. AI generate — preset-authoritative (✅ đã ship, MC-only)
Khi gen có `part_code` (optional trong request `/section`):
1. structure = `presets.structure_facts(preset)` (đè counts/options/word-count/
   CEFR) → khe `STRUCTURE SPEC` sẵn có.
2. `reshape_per_question(spec, N)` — căn skill-map của ANALYZE (dài = số câu
   source M) về đúng N **bằng code** (N<M lấy mẫu đều, N>M lặp vòng).
3. `_merge_generated_section(preset=)` ép số câu/type/points theo preset (đè
   source).
4. `_validate_section_structure(preset_skeleton(preset), …)` (Tầng B vs khuôn) +
   `validate_output_against_preset` (mã lỗi field).
5. Giữ nguyên ANALYZE/leak/similarity/blind-solve/FIX. **Prompt diff = rỗng.**

`report.sections[].part_code` ✅ provenance. Phạm vi: **Mode-2 một part**; Mode-1
cả-đề/preview chưa nhận part_code (🔨 B8, cần map theo section).

## 4. Builder & scaffold (🔨 — phần khiến doc trước thiếu)
Tầm nhìn client (§3): tạo section/đề theo khuôn, server scaffold, FE khóa cấu
trúc. Backend cần (chi tiết ở §7):
- 🔨 B2 `part_code` persist trên `sections` (+ `format_standard` trên `exams`) —
  **migration 0024**.
- 🔨 B3 `POST /api/sections` nhận `part_code` ⇒ scaffold section rỗng đúng khuôn.
- 🔨 B4 `POST /api/exams/scaffold` ⇒ tạo đề + đủ section preset, unpublished.
- 🔨 B5 gắn `preset_validator` vào builder-save (POST/PUT section) → field errors.

## 5. Eligibility surface (🔨 B6)
Amendment §4.2: API generate trả `{mode, core, eligibility_reason}` per section ở
preview, cấm fallback im lặng. Hiện `mode` có; `core`/`eligibility_reason` chưa
surface → `spec_mode.assign_core` cần trả lý do + service thread vào report.

## 6. Hardening + model (✅ đã ship)
- `AI_REQUEST_TIMEOUT=180s` + `AI_MAX_RETRIES=2` → mọi call (2 adapter).
- Default model `anthropic/claude-opus-4.8` (catalog + env). Resolve: request >
  DB `ai_generation_settings` > env (⚠️ đổi `.env`/DB nếu pin slug cũ). A/B
  re-baseline trên Opus 4.8 (sau).

## 7. BACKEND WORK-ITEMS để unblock FE ⭐
> Để mang ra chốt **thứ tự build**. "MC-only" chỉ áp cho AI-gen core; builder/
> scaffold/validator áp cho **mọi** preset.

| ID | Việc | Đụng | Migration | Đợt | Unblock FE |
|---|---|---|---|---|---|
| ✅ | GET /api/presets; `partCode` trên /section; report.part_code; preset-authoritative MC; timeout; Opus 4.8 | (đã merge `d7f3877`) | Không | — | dropdown nguồn, AI-gen theo khuôn |
| **B1** | Mở rộng `PART_PRESETS` đủ 22 Part + field builder (`materials_spec`, `gap_markers`, `shared_options`, `instructions_en`, `default_position`, `ai_core`); mở rộng GET /api/presets trả thêm field | `services/presets.py`, `api/presets/schemas.py` | **Không** | 1 (nền) | dropdown phủ đủ Part; builder biết khung |
| **B2** | `sections.part_code` (nullable) + index + `exams.format_standard` | migration **0024** + `schema.sql` + section/exam loaders/serializers | **Có (0024)** | 1 | badge bền vững, validator-on-save, scaffold lưu khuôn |
| **B3** | `POST /api/sections` nhận `part_code` → scaffold section rỗng đúng khuôn (part_label/instructions/type/N câu rỗng/option/material placeholder) | `services/section_service`, `api/sections/*` | (dùng 0024) | 2 | "Thêm phần thi" theo khuôn |
| **B4** | `POST /api/exams/scaffold {level,skill,format_standard}` → tạo đề + đủ section preset, unpublished | `services/exam_service`, `api/exams/*` | (dùng 0024) | 2 | checkbox "Tạo sẵn khung Cambridge" |
| **B5** | Gắn `preset_validator.validate_output_against_preset` vào builder-save (POST/PUT section) → field errors có code | `services/section_service` | Không | 2 | lỗi validator inline |
| **B6** | `assign_core` trả `eligibility_reason`; thread `{mode, core, eligibility_reason}` per section vào preview/section report | `services/ai/spec_mode.py`, `exam_generation_service.py` | Không | 1–2 | hiển thị core + "vì sao rewrite" |
| **B7** | (tùy chọn) endpoint/catalog liệt kê validator code→message mặc định | `api/presets` hoặc doc | Không | 2 | nguồn mã lỗi tập trung |
| **B8** | (sau) `part_code` map cho AI-gen Mode-1 cả-đề/preview | `exam_generation_service.py`, routes, job | Không | sau | AI-gen cả đề theo khuôn |
| ⏳ | Multi-core (cloze/matching/gapped/listening) + render_hint + listening audio-pending | nhiều | có thể | sau | dạng đề ngoài MC |

## 8. Design decisions / trade-offs
| Quyết định | Chọn | Bỏ | Vì sao |
|---|---|---|---|
| Nguồn cấu trúc AI-gen | **Preset đè source** | source-authoritative | Đề ra chuẩn Cambridge, không phụ thuộc đề mẫu |
| Ánh xạ per_question N≠M | **reshape code, prompt FROZEN** | sửa ANALYZE prompt | Không đụng prompt client; deterministic, test được |
| Lưu preset | **code constant** | DB table | Format Cambridge ⇒ git |
| Phạm vi AI-gen core | **MC-only** | đủ core ngay | Tránh 2 mặt trận; multi-core là amendment riêng |
| Builder vs source | **builder/scaffold cho MỌI preset** | chỉ MC | Khung là cấu trúc thuần, không cần AI-core |
| part_code (AI-gen) | **optional request, chưa persist** | persist ngay | Persist là B2 (phục vụ builder), tách rõ |

## 9. Kiểm thử (đã có cho phần ✅)
Unit: presets/reshape/validator; integration mock: source 3q/4opt + KET_R_P3 ⇒
5q/3opt; smoke Opus 4.8 1 part ⇒ 5q/3opt, trigram 0.0, word-count in range. Full
suite xanh; prompt diff rỗng. (B1–B8 sẽ kèm test khi build.)
