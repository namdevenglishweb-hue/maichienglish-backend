---
feature: exam-part-presets
type: design
status: draft
last-updated: 2026-06-13
author: backend
depends-on: [exam-ai-generation, exam-gen-v3-spec-mode]
changelog: DRAFT 2026-06-13 — MC preset-authoritative structure (preset đè đề
  gốc về số câu/option/word-count/CEFR); reshape per_question bằng code; prompt
  FROZEN; giữ nguyên ANALYZE/leak/blind-solve/similarity; request-timeout 180s;
  default model Opus 4.8
---

# Part Presets (MC-only) — Design / Core feature

> **Một dòng:** thêm tầng PRESET = định nghĩa cứng của một "Part" Cambridge
> (số câu, số option, word-count, CEFR…). Khi gen có `part_code`, **preset là
> nguồn cấu trúc duy nhất — đè đề gốc** (đề gốc bao nhiêu câu cũng kệ). Bù vào
> đó, số liệu kỹ năng per-question do ANALYZE sinh được **reshape bằng code**
> về đúng số câu của preset. **Không sửa một ký tự prompt nào.**
>
> **Phạm vi đợt này:** chỉ core `multiple_choice` (PET_R_P3, KET_R_P3). Các
> dạng khác (cloze/matching/listening…) là multi-core, để amendment sau.

---

## 1. Vấn đề

Spec mode v3 lấy cấu trúc (số câu/option) **từ đề gốc** rồi ép nội dung sinh ra
khớp đề gốc đó. Hệ quả: đề mới luôn copy *số câu* của đề mẫu — không thể "muốn
ra Part chuẩn 5 câu" nếu đề mẫu có 6 câu. Client chốt hướng: **cấu trúc phải
theo CHUẨN CAMBRIDGE (preset), không theo đề mẫu.**

## 2. Kiến trúc — preset là LỚP THÊM, không phải pipeline mới

```
part_code ─► PartPreset (code constant) ─┐
                                         ├─► structure_facts  → STRUCTURE SPEC slot (data)
                                         ├─► reshape_per_question(N) → PER-QUESTION slot (data)
                                         ├─► _merge_generated_section(preset=) → ép count/option/points
                                         ├─► preset_skeleton → Tầng B so cấu trúc (thay source)
                                         └─► validate_output_against_preset → mã lỗi rõ
GIỮ NGUYÊN 100%: ANALYZE(source) → leak-check(source) → topic+seed → GENERATE
   → shuffle → trigram-guard(vs source) → blind-solve VERIFY → FIX → all-or-nothing
```

Không có `part_code` ⇒ **hành vi y hệt v3 cũ** (source-driven), backward-compatible.

## 3. Data model — code constant (KHÔNG DB)

`services/presets.py`: `PartPreset` (frozen dataclass). Lưu ở git (review/diff,
deploy cùng code + prompt + harness), KHÔNG ở DB (admin không sửa runtime —
khác `section_type_prompts`).

```
PartPreset: part_code, level, skill, section_type, question_type,
            num_questions, options_per_question, word_count_range (min,max),
            cefr_level, points_per_question, label, label_vi,
            per_question: tuple[QuestionProfile, ...]   # rỗng đợt này
```

Đợt này: `PET_R_P3` (5 câu / 4 opt / 220–320 từ / B1), `KET_R_P3`
(5 câu / 3 opt / 150–230 từ / A2). `per_question` (skill profile từng câu) là
field **cross-check/audit tương lai — KHÔNG nạp vào prompt** đợt này.

## 4. Tích hợp pipeline (1 điểm cắm)

Trong `_generate_section_spec`, khi có preset:
1. **structure** = `presets.structure_facts(preset)` (counts/options/word-count/
   CEFR/type từ preset) — `merge_structure` overlay lên phần định tính ANALYZE
   (text_genre/style giữ nguyên).
2. **per_question** = `spec_mode.reshape_per_question(spec, preset.num_questions)`
   — căn số liệu kỹ năng ANALYZE (dài = số câu source M) về đúng N.
3. **merge** = `_merge_generated_section(..., preset=preset)` ép số câu = N,
   `question_type`/`points` từ preset, position 1..N (không tham chiếu source).
4. **guardrail** = `validate_output_against_preset` (mã lỗi field) → fail =
   StructureMismatch vào retry loop.
5. **Tầng B** = `_validate_section_structure(preset_skeleton(preset), section)`
   — so invariant với khung preset thay vì source (logic checker KHÔNG đổi).
6. FIX (vòng 2 blind-solve) cũng re-merge qua nhánh `preset=`.

## 5. Reshape per_question (giải "rủi ro #1" bằng code)

ANALYZE sinh `per_question` khớp số câu **source M**; preset cần **N**. Thay vì
sửa prompt, code căn lại:
- `N == M`: giữ nguyên (đánh số 1..N).
- `N < M`: lấy mẫu đều `round(i*(m-1)/(n-1))` (giữ đa dạng kỹ năng).
- `N > M`: lặp vòng `i % m`.
- per_question rỗng / `N<=0`: trả nguyên (prompt tự bỏ qua slot).
Luôn ra đúng N phần tử, position 1..N. Prompt chỉ `json.dumps` slot này ⇒
structure (N) và per_question (N) khớp nhau, **template bất biến**.

## 6. Bằng chứng PROMPT FROZEN

`git diff services/ai/prompts.py` = **rỗng**. Prompt generate/verify/fix nhận
`spec["structure"]`/`spec["per_question"]` dạng opaque JSON ⇒ đổi NGUỒN dữ liệu
(preset thay derive-from-source) **không** đụng wording. Đề gốc vẫn chỉ vào
ANALYZE; blind-solve vẫn strip key; mọi rule chống-leak/text_genre của client
giữ nguyên.

## 7. Hardening + model

- **Request timeout:** `AI_REQUEST_TIMEOUT=180s` + `AI_MAX_RETRIES=2` (settings)
  → áp vào `AsyncOpenAI`/`AsyncAnthropic` mọi call (analyze/gen/verify/fix). Lý
  do: SDK mặc định 600s/call từng gây stall ~nhiều phút khi A/B.
- **Model default:** `anthropic/claude-opus-4.8` (catalog + env default). Resolve
  order: per-request > DB `ai_generation_settings` > env. (⚠️ đổi `.env`/DB nếu
  đang pin slug cũ.) Số A/B cũ đo trên sonnet-4.5 → cần re-baseline.

## 8. Design decisions / trade-offs

| Quyết định | Chọn | Bỏ | Vì sao |
|---|---|---|---|
| Nguồn cấu trúc | **Preset (đè source)** | Source-authoritative; preset chỉ validate | Client: đề ra phải chuẩn Cambridge, không phụ thuộc đề mẫu |
| Ánh xạ per_question khi N≠M | **Reshape code (no prompt)** | Sửa ANALYZE để emit đúng N; bỏ per_question | Giữ prompt FROZEN + giữ tính năng skill K=3; reshape deterministic, test được |
| Lưu preset | **Code constant** | Bảng DB | Format Cambridge, đổi cùng core/prompt/harness ⇒ thuộc git |
| Cần đề gốc? | **Vẫn bắt buộc** | Bỏ source (preset+topic) | Giữ ANALYZE/leak/similarity nguyên vẹn; "preset+topic không source" là mode riêng tương lai |
| part_code | **Optional trong request, không persist** | Cột `sections.part_code` + migration | Đợt này chỉ gen Mode-2; builder/persist là amendment sau |
| Guardrail | **2 lớp** (Tầng-B skeleton enforce + validator messages) | 1 lớp | Skeleton chặn cứng; validator cho thông điệp field rõ + tái dùng audit |

## 9. Ngoài phạm vi (đợt sau)
Multi-core (cloze/matching/gapped/listening) · builder UI + scaffold + cột
`part_code` persist (migration) · Mode-1 cả-đề/preview theo preset (cần map
part_code từng section) · per_question preset đẩy vào prompt · A/B re-baseline
trên Opus 4.8.

## 10. Kiểm thử
Unit: `presets` (resolve/facts/skeleton/list), `reshape_per_question` (mọi
edge), `preset_validator`. Integration (mock): source 3q/4opt + preset KET_R_P3
⇒ output **5q/3opt** theo preset, qua Tầng-B-skeleton + blind-solve. Smoke thật
Opus 4.8: 1 part KET_R_P3 ⇒ 5 câu/3 option, trigram 0.0, word-count trong range.
Full suite xanh; prompt diff rỗng.
