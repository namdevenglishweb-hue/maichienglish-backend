---
feature: exam-part-presets
type: design
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: 2026-06-13 (rev3) — B3–B7 SHIPPED. Full preset catalogue (B1),
  part_code/format_standard persist + migration 0024 (B2), scaffold-section (B3),
  scaffold-exam (B4), validate-on-save (B5), eligibility_reason (B6),
  error-codes endpoint (B7). B8 + multi-core remain ⏳.
---

# Part Presets — Design / Core feature

> **Một dòng:** PRESET = định nghĩa cứng của một Part Cambridge (số câu/option/
> word-count/CEFR…), một nguồn sự thật dùng chung cho **builder**, **validator**,
> và **AI generate**. AI-gen: preset đè đề gốc về cấu trúc (✅ Part MC). Builder:
> scaffold section/đề đúng khuôn + validate-on-save (✅, mọi Part). AI-gen đa-core
> / cả-đề (⏳).
>
> **Nguồn tầm nhìn:** amendment `AMENDMENT_part-presets_multi-core.md` của client
> (§2 preset, §3 builder flow, §4.2 eligibility, §6 listening). Kiến trúc lõi
> v1.1/v1.2/v3 (ANALYZE→leak→generate→shuffle→similarity→blind-solve→FIX→
> all-or-nothing) **GIỮ NGUYÊN — prompt FROZEN.**

## Bảng trạng thái
✅ ĐÃ SHIP (B1–B7, commits `b5be508`/`fdab6e8`/`978ad90`) · ⏳ ĐỂ SAU (B8 +
multi-core).

---

## 1. Vấn đề
(a) Builder tự do ⇒ không có gì chặn "Part 1 KET" sai khung. (b) AI-gen lấy cấu
trúc theo đề mẫu nên không ra được Part chuẩn nếu đề mẫu lệch. Gốc chung: **chưa
có định nghĩa máy-đọc-được của từng Part.** → tầng PRESET.

## 2. Preset — một nguồn sự thật (code constant) — ✅ B1
`services/presets.py`, `PartPreset` frozen dataclass, KHÔNG DB (format Cambridge,
đổi cùng core/prompt/harness ⇒ thuộc git). ✅ **đủ Part KET/PET** (Reading/
Listening/Writing/Speaking) với field builder: `materials_spec`, `gap_markers`,
`shared_options`, `instructions_en`, `default_position`, `ai_core`, `per_question`.
- AI-gen chỉ chạy preset có `ai_core ∈ AI_GEN_CORES` (= `{"multiple_choice"}` đợt
  này ⇒ `PET_R_P3` 5q/4opt/B1; `KET_R_P3` 5q/3opt/A2). `supports_ai_gen()` +
  `aiGenSupported` flag. Builder/scaffold/validator áp cho **mọi** Part.
- `GET /api/presets` ✅ trả full catalogue (mọi field trên + `aiGenSupported`,
  `imageDependent`). Word-count (verify Cambridge 2025/2026 = format 2020 vẫn
  current): `PET_R_P3 (300,400)`, `KET_R_P3 (200,280)` (đã nới sát mẫu thật).

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

Guard: `part_code` của Part **không** `aiGenSupported` → 400 ("chưa hỗ trợ
AI-gen"). `report.sections[].{part_code, mode, core, eligibility_reason}` ✅
provenance. Phạm vi: **Mode-2 một part**; Mode-1 cả-đề/preview chưa nhận part_code
(⏳ B8, cần map theo section).

## 4. Builder & scaffold — ✅ B2/B3/B4/B5
- **B2** ✅ `sections.part_code` (nullable+index) + `exams.format_standard` —
  **migration 0024** (đã apply dev DB); loaders/serializers đọc-ghi; create_exam_nested
  persist. Backward-compat (NULL = section/đề cũ).
- **B3** ✅ `POST /api/presets/scaffold-section { partCode }` (auth teacher/admin)
  → trả `{ section: <template> }` **không persist** (section cần exam; persist khi
  lưu qua create_exam_nested). Placeholder qua hết validator (options A/B/…,
  `correct_index:0`, fill_blank `["?"]`, gap markers cho cloze, media
  `pending://replace`+`pendingReplacement`, picture-MC options = image_url).
- **B4** ✅ `POST /api/exams/scaffold { level, skill, formatStandard?, title? }`
  (auth teacher/admin) → exam unpublished + đủ section, **reuse create_exam_nested**
  (1 transaction, không orphan). `skill` ∈ reading/listening (exam.skill CHECK).
- **B5** ✅ validate-on-save: section có `part_code` **kèm câu hỏi** lệch khuôn →
  HARD-BLOCK 400 field-coded (`assert_section_matches_preset`) ở create_exam_nested
  + create_section_with_questions. Bare granular create/update/bulk: persist +
  known-code check (cấu trúc deferred — policy gap-marker). `partCode` thêm vào
  `SectionCreate`/`SectionUpdate`.

## 5. Eligibility surface — ✅ B6
`spec_mode.assign_core_with_reason()` trả `(core, reason)`; `generate_one_section`
thread `eligibility_reason` vào `report.sections[]` (cả spec lẫn rewrite fallback);
`_SPEC_REPORT_KEYS` propagate sang preview/job. Reason structural-only (không lộ
text đề). `assign_core` giữ nguyên (gọi lại hàm mới).

## 6. Hardening + model (✅ đã ship)
- `AI_REQUEST_TIMEOUT=180s` + `AI_MAX_RETRIES=2` → mọi call (2 adapter).
- Default model `anthropic/claude-opus-4.8` (catalog + env). Resolve: request >
  DB `ai_generation_settings` > env (⚠️ đổi `.env`/DB nếu pin slug cũ). A/B
  re-baseline trên Opus 4.8 (sau).

## 7. WORK-ITEMS — trạng thái ⭐
"MC-only" chỉ áp cho AI-gen core; builder/scaffold/validator áp cho **mọi** Part.

| ID | Việc | Trạng thái | Endpoint/đụng |
|---|---|---|---|
| B1 | `PART_PRESETS` đủ Part KET/PET + field builder; `GET /api/presets` (+`aiGenSupported`/`imageDependent`) | ✅ `b5be508` | `services/presets.py`, `api/presets/schemas.py` |
| B2 | `sections.part_code` + index + `exams.format_standard`; loaders/serializers; persist | ✅ `b5be508` | **migration 0024** (applied dev), `schema.sql`, exam/section service |
| B3 | scaffold MỘT section rỗng-hợp-lệ từ preset (template, không persist) | ✅ `fdab6e8` | `POST /api/presets/scaffold-section` |
| B4 | scaffold CẢ ĐỀ (reuse create_exam_nested) | ✅ `fdab6e8` | `POST /api/exams/scaffold` |
| B5 | validate-on-save (hard-block 400, field-coded) | ✅ `978ad90` | `exam_service`/`section_service` |
| B6 | `eligibility_reason` per section trong report | ✅ `978ad90` | `spec_mode`, `exam_generation_service` |
| B7 | endpoint mã lỗi validator → message EN/VI | ✅ `978ad90` | `GET /api/presets/error-codes` |
| **B8** | AI-gen Mode-1 cả-đề/preview theo `part_code` (map từng section) | ⏳ ĐỂ SAU | `exam_generation_service`, routes, job |
| **⏳** | Multi-core AI (cloze/matching/gapped/listening) + render_hint + listening audio-pending | ⏳ ĐỂ SAU | nhiều (có thể migration) |

Cũng đã ship: request-timeout 180s + `AI_MAX_RETRIES=2` (2 adapter); default model
`anthropic/claude-opus-4.8` (catalog + env) — xem §6.

## 8. Design decisions / trade-offs
| Quyết định | Chọn | Bỏ | Vì sao |
|---|---|---|---|
| Nguồn cấu trúc AI-gen | **Preset đè source** | source-authoritative | Đề ra chuẩn Cambridge, không phụ thuộc đề mẫu |
| Ánh xạ per_question N≠M | **reshape code, prompt FROZEN** | sửa ANALYZE prompt | Không đụng prompt client; deterministic, test được |
| Lưu preset | **code constant** | DB table | Format Cambridge ⇒ git |
| Phạm vi AI-gen core | **MC-only** | đủ core ngay | Tránh 2 mặt trận; multi-core là amendment riêng |
| Builder vs source | **builder/scaffold cho MỌI preset** | chỉ MC | Khung là cấu trúc thuần, không cần AI-core |
| part_code persist | **cột `sections.part_code` (mig 0024)** | nhét vào jsonb | Query/filter được; builder lưu khuôn từng section |
| B3 scaffold | **trả template, không persist** | tạo section rời | Section cần exam; persist khi lưu qua create_exam_nested |
| validate-on-save | **hard-block 400** structural; granular partial = known-check | warn / chặn cả granular | Giữ chuẩn Cambridge; partial section hợp lệ có 0 câu (policy gap-marker) |
| materials check | **bỏ khỏi validator** (chỉ check type/count/option/qtype) | check số material theo spec | Material đa dạng 30 Part; AI-gen vẫn được Tầng-B `preset_skeleton` ép 1 text |

## 9. Kiểm thử
Unit (no DB): presets/reshape/validator/scaffold mọi Part qua validator;
assign_core_with_reason; error_code_catalog; assert_section_matches_preset
(no-op/unknown/partial/lệch). Integration mock: source 3q/4opt + KET_R_P3 ⇒
5q/3opt. Dev-DB smoke: scaffold-exam KET Reading ⇒ 5 part/30 câu; validate-on-save
part_code lệch ⇒ ValidationError(`PRESET_NUM_QUESTIONS`); round-trip part_code/
format_standard. Full suite xanh (156); prompt diff rỗng.
