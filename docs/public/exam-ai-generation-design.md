---
feature: exam-ai-generation
type: design
status: approved
last-updated: 2026-06-10
author: backend
depends-on: [exam, section, question, attempt-lifecycle]
---

# Exam AI Generation — Sinh đề tương tự (Design / Core feature)

> ⚠️ **CẬP NHẬT 2026-06-12 — v3 SPEC MODE đã implement** (xem
> `docs/exam-gen-v3-spec-mode/exam-gen-v3-spec-mode-design.md`, APPROVED).
> Các mục dưới đây của doc này được v3 SỬA ĐỔI khi `promptVersion="v3"`:
> - **§3 (thang K):** với v3, K = lượng spec giữ lại (K1–2 rewrite có source /
>   K3 giữ skill từng câu / K4 structure+style / K5 structure-only) — label UI
>   phải theo version. v1/v2 giữ nguyên thang cũ.
> - **§6.1 (payload):** spec mode KHÔNG gửi section gốc vào generate;
>   exam_context bị cắt còn level+skill (cấm title/description).
> - **§7 (self-review):** verify per-mode — spec mode chấm theo STRUCTURE
>   spec, không thấy source; rewrite giữ verify v2.
> - **§8 (Tầng B):** spec mode thêm word-count check (±15%) + trigram
>   similarity guard (>10% và ≥3 trigram chung, chạy TRƯỚC verify).
> - **§12 (config):** registry thêm v3 (spec_mode fields); bảng
>   `section_skill_maps` (migration 0023) cache ANALYZE; temperature 0.2
>   riêng cho analyze.
> - **§15 (decisions):** xem bảng 19 decisions trong doc v3.

> Từ **1 exam gốc**, quét **lần lượt từng section**, gửi data + mô tả + tham số **K** đến **Claude**,
> nhận về 1 section **cùng cấu trúc nhưng đổi nội dung**, validate, lắp lại thành cây đề và ghi
> **1 đề draft mới** qua `create_exam_nested`.
>
> **Hai phase:**
> - **Phase 1 — Engine (core):** logic sinh chạy được bằng **script background** (`scripts/generate_similar_exam.py`), không cần FE. Mô tả ở §1–13.
> - **Phase 2 — FE/API:** mở cho Frontend qua **HTTP API admin-scoped**, chạy nền bằng **FastAPI BackgroundTasks** + bảng job để FE poll tiến trình. Mô tả ở **§14**. Script vẫn giữ làm đường "power-user".
> Lưu ý cho FE (semantics, journey) tách sang `exam-ai-generation-frontend.md`.
>
> ⭐ **Bất biến chất lượng (quan trọng nhất):** đây là đề thi tiếng Anh thật, nên **dù K=1 hay K=5**,
> đầu ra mỗi section phải **chuẩn như đề thật** — material ↔ question nhất quán, đáp án **đúng**,
> đúng dạng/độ khó của level (KET/PET/…). Vì vậy AI **bắt buộc tự kiểm tra lại 1–2 vòng** (self-review
> ngữ nghĩa) trước khi báo hoàn thành. Đây là phần lõi, tách bạch với các kiểm tra cấu trúc bằng code.

---

## 1. Mục tiêu & sub-features (B1)

| # | Sub-feature | Mô tả |
|---|---|---|
| 1 | Load đề gốc kèm đáp án | Đọc cây Exam→Section→Question **không strip** đáp án (AI cần thấy đáp án để viết lại nhất quán). |
| 2 | Per-section AI rewrite | Mỗi section → 1 lời gọi Claude (tool-use, structured output) sinh section mới theo K. |
| 3 | **AI self-review ngữ nghĩa (1–2 vòng)** | Sau khi sinh, AI **tự kiểm tra lại** material↔question coherence + đáp án đúng + đúng dạng đề; phát hiện lỗi thì **tự sửa**; lặp tới khi "đạt" hoặc hết số vòng. **Bắt buộc**, tách khỏi sub-feature 4. |
| 4 | Validate cấu trúc + retry (bằng code) | Validate output bằng validator có sẵn + structural-invariant checker; lỗi cứng → re-prompt tối đa 2 lần. |
| 5 | **Hoàn chỉnh toàn đề (all-or-nothing)** | Đủ N section như đề gốc, mỗi section complete 100%; section lỗi không cứu được ⇒ abort, không tạo đề nào (§9). |
| 6 | Assemble + persist | Gom cây, validate toàn bộ, ghi 1 transaction qua `create_exam_nested` (đề mới luôn `is_published=false`). |
| 7 | Media meta (song hướng) | `material.meta`: đề gốc cung cấp `transcript`/`description` làm **input** cho AI; đề mới nhận transcript/description mới + `pendingReplacement` làm **output** (§5). Strip khỏi payload student. |
| 8 | **Admin prompt: per-type + per-section** | (A) config `section_type_prompts` theo type (bền) + (B) `sectionPrompts` ad-hoc cho từng section tại lúc gen; cả hai chèn thêm vào hướng dẫn AI (§10). |
| 9 | Generation report | Tổng hợp: section ok, kết quả self-review, media-TODO, retry, token usage; lưu vào `exams.generation_meta` (audit) + in ra console. (Transcript mới nằm trong `material.meta`, không ở report — §5.) |
| 10 | **Lõi chung + 2 mode** | Đóng gói gen-1-part thành `generate_one_section` (§2.1); **Mode 1** cả-đề-tự-lưu (sub-feature 5–6) + **Mode 2** tương-tác-từng-part (FE staging, admin tự Save) cùng dùng lõi đó (§2.2). |

> **Hai tầng kiểm tra khác nhau — đừng nhầm:**
> - **Tầng A — AI self-review (ngữ nghĩa):** chính AI đánh giá "section này có chuẩn như đề thật không, đáp án có đúng không" → tự sửa. Đây là yêu cầu chất lượng cốt lõi (sub-feature 3).
> - **Tầng B — kiểm tra cấu trúc (bằng code, tất định):** ta dùng validator + structural-invariant checker để chặn AI phá shape/số câu/số option/URL media. Không đánh giá đúng/sai ngữ nghĩa.
> Một section chỉ được coi là "hoàn thành" khi **qua cả hai tầng**.

**Không thuộc phạm vi:** tự tái tạo **file** audio/ảnh thật (chỉ sinh `transcript`/`description` dạng text — §5); auto-resume job sau khi process restart; server-side staging cho Mode 2 (dùng client staging — §2.2); teacher/non-admin tự gen.

---

## 2. Kiến trúc tổng thể — lõi chung + 2 mode

### 2.1 Lõi tái sử dụng: `generate_one_section` ⭐

Toàn bộ logic gen **1 part** được đóng gói thành **1 hàm dùng chung** — cả hai mode đều gọi:

```
generate_one_section(source_section, k, type_prompt, section_prompt) -> validated_section
   ┌─────────────── per-section pipeline ───────────────┐
   │ a. generate          (AI: rewrite theo K + prompt A type + B section, §10) │
   │ b. self-review ×1–2  (AI: tự soi → tự sửa)            │  ← Tầng A (ngữ nghĩa, §7)
   │ c. validate cấu trúc (code) → lỗi cứng thì re-gen     │  ← Tầng B (tất định, §8)
   └──────────────────────────────────────────────────────┘
   → trả về section payload đã đạt cả 2 tầng; raise nếu hết budget (§9.2)
```

### 2.2 Hai mode tiêu thụ lõi đó

```
                 ┌────────────────────────── generate_one_section (lõi §2.1) ──────────────────────────┐
                 │                                                                                       │
  MODE 1 (cả đề, tự lưu — GIỮ NGUYÊN)                         MODE 2 (tương tác từng part — MỚI)
  generate_similar_exam(...)                                  • gen 1 part   → trả section (KHÔNG lưu)
   • loop lõi qua MỌI section                                 • gen hết 1 lần (preview) → trả N section (KHÔNG lưu)
   • all-or-nothing (§9)                                      • FE giữ part ở "cột phải" (client staging)
   • tự create_exam_nested → đề draft                         • admin bấm Save → persist (assemble §14.5)
```

| | Mode 1 — cả đề | Mode 2 — từng part |
|---|---|---|
| Ai lắp ráp | server | **FE** (cột phải) |
| Lưu đề | **tự động** cuối job | **chỉ khi admin bấm Save** |
| Hoàn chỉnh | **all-or-nothing** (đủ N hoặc không gì) | **per-part** (part nào fail thì retry riêng; admin tự quyết đủ chưa) |
| Dùng khi | muốn nhanh, 1 phát ăn ngay | muốn xem/chỉnh/gen lại từng part trước khi lưu |

### 2.3 Tầng dưới (chung cho cả 2 mode)

```
services/exam_generation_service.py   ── generate_one_section(...)  ← lõi
   ├── generate_similar_exam(...)        Mode 1: loop lõi + all-or-nothing + create_exam_nested
   ├── generate_sections_preview(...)    Mode 2: loop lõi, trả sections[] (per-part status, KHÔNG lưu)
   └── assemble_generated_exam(...)      Mode 2 Save: validate sections[] FE gửi → create_exam_nested + provenance
        ▼
services/ai/generator.py   ── AIContentGenerator (ABC) + get_ai_generator()   (factory theo AI_PROVIDER)
   • generate_section(payload, k) -> section dict
   • verify_section(section, payload, k) -> {is_acceptable, issues[], fixed_section?}
        ▼
services/ai/adapters/anthropic_generator.py   ── AsyncAnthropic, tool-use forced, prompt caching
```

**Tách tầng** y như storage (`storage_service.py` + `adapters/`): service nghiệp vụ không biết provider; chọn provider/model qua factory `get_ai_generator(provider=, model=, max_tokens=)`, resolve **per-request > DB config > env** (§12). 4 provider (openrouter/groq/gemini/anthropic).

**Fail-fast (Mode 1):** toàn bộ section sinh + validate **trong bộ nhớ trước**, chỉ ghi DB **một lần** qua `create_exam_nested` (1 transaction). Lỗi giữa chừng ⇒ **không tạo đề rác**. (Mode 2 khác: xem §9.6.)

---

## 3. Tham số K (thang 1–5 rời rạc)

| K | Mức độ | Chỉ dẫn cho AI |
|---|---|---|
| 1 | Tối thiểu | Chỉ đổi **tên riêng / số / địa danh**. Giữ chủ đề, độ khó, độ dài, cấu trúc câu. |
| 2 | Nhẹ | Đổi tên + vài chi tiết phụ + viết lại lác đác vài câu. Giữ chủ đề tổng thể. |
| 3 | Vừa | **Đổi chủ đề / bối cảnh** (vd đá bóng → cầu lông). Giữ độ khó, độ dài, dạng câu hỏi. |
| 4 | Mạnh | Bối cảnh mới + viết lại gần hết, có thể đổi cấu trúc câu. Giữ độ khó + số câu. |
| 5 | Gần như mới | Viết lại như đoạn văn mới cùng dạng/độ khó. Chỉ giữ **cơ chế cấu trúc** (số câu, loại, cách chấm). |

K được map sang 1 đoạn chỉ dẫn ngôn ngữ tự nhiên (`services/ai/prompts.py::K_INSTRUCTIONS[k]`) nhét vào prompt. K **không bao giờ** được phép nới lỏng các bất biến ở §4.

---

## 4. Bất biến cấu trúc vs Nội dung được đổi

### 4.1 GIỮ NGUYÊN — structural invariants (AI không được phá; ta tự kiểm tra lại)

- Số section, thứ tự, `type`, `max_audio_plays`.
- Mỗi section: **số material + thứ tự + type của từng material**; **URL của audio/image giữ y nguyên byte-for-byte** (meta thì được đổi — xem §4.2).
- Số câu hỏi/section + thứ tự + `question_type` + `points`.
- MC/matching: **số option giữ nguyên**; `correct_index` phải nằm trong `[0, len(options))`.
- fill_blank: số phần tử `correct_answers` khớp số gap; mọi `{{gap:N}}` trong text resolve về 1 vị trí câu hỏi hợp lệ. *(Riêng section `form_completion`: question_type vẫn `fill_blank` nhưng **không** dùng `{{gap:N}}` — mỗi blank là 1 câu, layout/label/prefix/postfix nằm trong `question_data`; giữ nguyên số blank + prefix/postfix, xem §10.1.)*

### 4.2 CHO ĐỔI — content (theo K)

- `part_label`, `instructions` (đổi nhẹ cho khớp nội dung mới).
- Nội dung `text` material (đoạn văn, đề bài, `{{gap:N}}` giữ nguyên marker).
- **`audio.meta.transcript` / `image.meta.description`**: AI viết mới (nội dung media tưởng tượng) + đặt `meta.pendingReplacement=true` (§5). URL không đổi.
- MC: `stem`, `options[].text`, `correct_index` (được phép đổi đáp án đúng **miễn nhất quán** với nội dung mới).
- fill_blank: `correct_answers` viết lại khớp text mới.

### 4.3 Structural-invariant checker (pure function — test kỹ)

`exam_generation_service._assert_structure_preserved(original_section, generated_section)`:
1. `type`, `max_audio_plays` bằng nhau.
2. `len(materials)` bằng nhau; với từng material: `type` bằng nhau; nếu `audio`/`image` thì `url` **bằng nhau tuyệt đối** (riêng `meta.transcript`/`meta.description`/`pendingReplacement` **không** so sánh — chúng được phép đổi, §5).
3. `len(questions)` bằng nhau; từng câu: `question_type` + `points` bằng nhau; với MC/matching `len(options)` bằng nhau và `correct_index` hợp lệ.
4. Số `{{gap:N}}` trong text khớp giữa cũ và mới.

Vi phạm bất kỳ ⇒ coi như AI fail ⇒ retry (§8).

---

## 5. Xử lý media (audio/ảnh) — meta đi theo material ⭐

### 5.1 Material meta: 1 field, dùng 2 chiều (input ↔ output)

Audio/ảnh là file thật mà AI **không nghe/không thấy hiệu quả**. Giải pháp: gắn **meta dạng text ngay trên material** (trong `materials` jsonb, **không thêm cột**):

```jsonc
{ "type":"audio", "url":"...", "label":"...",
  "meta": { "transcript": "đoạn nghe dạng text...", "pendingReplacement": false } }

{ "type":"image", "url":"...", "alt":"...", "label":"...",
  "meta": { "description": "ảnh mô tả gì để AI hiểu...", "pendingReplacement": false } }
```

Cùng một field phục vụ **hai chiều**:

| Chiều | Trên đề… | Vai trò |
|---|---|---|
| **INPUT** | đề **gốc** | `transcript`/`description` là **nguyên liệu** để AI hiểu nội dung nghe/nhìn khi viết lại section (đưa transcript hiệu quả hơn đưa file). |
| **OUTPUT** | đề **mới** | AI viết `transcript`/`description` **mới** cho media tưởng tượng + đặt `pendingReplacement=true` ⇒ vừa là *suggested transcript* cho feature tạo audio sau này, vừa là cờ "cần thay media". |

### 5.2 Quy tắc khi gen

- `url` của audio/image **giữ y nguyên** (file cũ); chỉ `meta.transcript`/`meta.description` được AI viết mới + `meta.pendingReplacement=true`.
- `media_todos` (trong report) **suy ra** từ các material có `pendingReplacement=true` — không lưu trùng transcript ở `exams.generation_meta`.
- Đề mới ở **draft** tới khi admin thay media (đổi `url` + `pendingReplacement=false`) + review xong → publish thủ công.

### 5.3 PRECONDITION: đề gốc thiếu meta ⇒ KHÔNG cho làm đề gốc (chặn từ đầu)

AI cần `transcript`/`description` làm nguyên liệu cho section nghe/nhìn. Vì vậy **trước khi gen** (tại bước validate POST / đầu script), kiểm tra:

> **Mọi material `audio` phải có `meta.transcript` (non-empty), mọi material `image` phải có `meta.description` (non-empty)** trong toàn bộ section active của đề gốc.

- Thiếu bất kỳ cái nào ⇒ **từ chối ngay** (KHÔNG tạo job, KHÔNG tốn token): lỗi **400** liệt kê rõ section/material còn thiếu (vd `"section 3 material 0 (audio) thiếu transcript"`).
- Admin phải **bổ sung meta vào đề gốc** (trong màn soạn đề) rồi mới gen lại.
- Lý do chọn chặn (thay vì copy verbatim): giữ tính toàn vẹn — đề mới phải được vary **mọi** section; không để lẫn section nghe/nhìn y hệt đề gốc. Đồng thời ép dữ liệu nguồn sạch (có transcript) — chính là nguyên liệu cho feature tạo audio sau này.

> Dữ liệu prod hiện tại audio chỉ có `{url, label}` (chưa transcript) ⇒ các đề đó **chưa đủ điều kiện** làm đề gốc cho tới khi admin điền meta.

### 5.4 BẮT BUỘC: strip material `meta` khỏi payload học sinh

`meta.transcript` = **đáp án bài nghe**; `meta.description` có thể lộ đáp án ⇒ **student KHÔNG được thấy**. Phải **strip `material.meta`** khỏi mọi payload phía student (exam published cho student, attempt start/active/detail). Admin (soạn đề) giữ nguyên để xem/sửa. Đây là thay đổi chạm sang serializer exam/attempt (xem §11.1) — **không chỉ riêng feature này**.

---

## 6. Section payload & AI tool schema

### 6.1 Payload gửi AI (cho mỗi section)

```jsonc
{
  "k": 3,
  "exam_context": { "level": "KET", "skill": "reading", "title": "KET READING 01" },
  "section": {
    "type": "fill_blank",
    "part_label": "Part 4",
    "instructions": "...",
    "max_audio_plays": null,
    "materials": [ /* nguyên shape text/image/audio, kèm index; audio/image KÈM meta.transcript/description nếu đề gốc có (§5) */ ],
    "questions": [ /* nguyên shape, KÈM correct_index/correct_answers */ ]
  }
}
```

> Material `meta.transcript`/`meta.description` của đề gốc là **nguyên liệu input** quan trọng nhất cho section nghe/nhìn — đề gốc thiếu meta sẽ bị **từ chối từ đầu** (§5.3), nên tới bước này luôn có meta.

### 6.2 Tool-use schema (ép Claude trả đúng shape)

Claude bị buộc gọi tool `emit_section` với JSON schema chặt khớp shape section (materials discriminated theo `type`; questions discriminated theo `question_type`):
- audio/image material: **bắt buộc** trả `meta.transcript`/`meta.description` mới + `meta.pendingReplacement=true` (§5); `url` giữ nguyên.
- mỗi câu MC: field phụ `answer_justification` (string ngắn) — map đáp án đúng ↔ bằng chứng trong text/transcript mới (dùng để log + kiểm tra nhẹ; **không** lưu vào questions).

Output của tool → ta **bóc field phụ (`answer_justification`) ra**, phần còn lại (kèm material.meta) đưa qua self-review (§7) rồi validator (§8).

> Prompt gửi AI gồm: system prompt (bất biến + quy tắc chất lượng) → K-instruction (§3) →
> **admin prompt (A) per-type + (B) per-section nếu có (§10)** → payload section. Thứ tự ưu tiên ở §10.4.

---

## 7. AI self-review — Tầng A (ngữ nghĩa) ⭐

> Mục tiêu: đảm bảo section đầu ra **chuẩn như đề thi thật** — đây là yêu cầu quan trọng nhất.
> Validator bằng code (§8) chỉ chặn được lỗi *cấu trúc*; **không** biết đáp án có đúng hay đoạn văn
> có khớp câu hỏi hay không. Phần đó giao cho AI tự soi.

### 7.1 Vòng lặp self-review (1–2 vòng, config `AI_SELF_REVIEW_ROUNDS`, default = 2)

Sau khi `generate_section` trả section nháp, với mỗi vòng (tối đa `AI_SELF_REVIEW_ROUNDS`):
1. Gọi `verify_section(section, payload, k)` — AI đóng vai **giám khảo độc lập**, KHÔNG phải người vừa viết, chấm theo checklist §7.2.
2. Nếu `is_acceptable = true` và `issues` rỗng ⇒ **dừng**, section đạt Tầng A.
3. Nếu có `issues` ⇒ AI trả luôn `fixed_section` (bản đã sửa theo đúng issue). Lấy `fixed_section` làm section hiện tại, **lặp vòng tiếp** để soi lại bản sửa.
4. Hết `AI_SELF_REVIEW_ROUNDS` mà vẫn còn issue nghiêm trọng (đáp án sai / câu không trả lời được từ material) ⇒ coi section **chưa đạt** ⇒ xử lý như lỗi cứng (§8: re-generate hoặc abort), và ghi `issues` cuối vào report.

> "1–2 lần kiểm tra rồi mới báo hoàn thành" = vòng lặp này. Mỗi section chỉ "done" khi self-review nói đạt **và** qua validator cấu trúc.

### 7.2 Checklist giám khảo (verify_section đánh giá)

- **Material ↔ question coherence:** mọi câu hỏi phải **trả lời được CHỈ bằng** material của section (đoạn đọc / transcript nghe gợi ý). Không có câu "mồ côi" không có căn cứ.
- **Đáp án đúng:** với MC/matching, `correct_index` thực sự là đáp án đúng theo nội dung mới; với fill_blank, mỗi `correct_answers[i]` đúng với gap tương ứng và **chấp nhận được mọi biến thể hợp lý** (hoa/thường theo `case_sensitive`).
- **Distractor hợp lý:** các option sai phải *gần đúng nhưng sai* (đúng kiểu đề thật), không lố bịch, không có 2 option cùng đúng.
- **Đúng dạng & độ khó:** giữ đúng phong cách level (KET/PET/IELTS…) và độ khó của section gốc.
- **Ngôn ngữ tự nhiên, không lỗi:** tiếng Anh đúng ngữ pháp/chính tả, mạch lạc; `{{gap:N}}` đặt ở vị trí hợp lý.
- **Tính nhất quán media nghe/nhìn:** câu hỏi listening/image phải khớp `material.meta.transcript`/`meta.description` mới (§5).

### 7.3 Output của `verify_section`

```jsonc
{
  "is_acceptable": false,
  "issues": [
    { "severity": "critical", "question_position": 3, "problem": "correct_index trỏ option B nhưng đoạn văn nói option C", "fix": "đổi correct_index=2" }
  ],
  "fixed_section": { /* section đã sửa, cùng shape emit_section */ }
}
```
- `severity`: `critical` (đáp án sai / câu vô căn cứ — bắt buộc sửa) hoặc `minor` (diễn đạt — nên sửa).
- Còn `critical` sau khi hết vòng ⇒ section chưa đạt.
- Số vòng đã chạy + issues cuối được ghi vào `generation_meta.self_review` (§11).

---

## 8. Validation cấu trúc & retry — Tầng B (tất định, bằng code)

Sau khi section qua Tầng A (§7), kiểm tra bằng code (không gọi AI):
1. `_validate_materials(materials)` — discriminated union text/image/audio.
2. `_validate_question_data(qtype, qdata)` — per-type (reuse).
3. `validate_gap_markers(materials, question_positions, ...)`.
4. `_assert_structure_preserved(original, generated)` (§4.3).

Lỗi bất kỳ ⇒ **re-generate** kèm thông báo lỗi cụ thể (tối đa **2 retry**/section; mỗi retry chạy lại cả Tầng A). Vẫn lỗi ⇒ **abort toàn bộ** (không tạo đề), report ghi rõ section + lỗi cuối. (Fail-fast: thà không tạo còn hơn tạo đề hỏng.)

---

## 9. Đảm bảo hoàn chỉnh toàn đề — all-or-nothing ⭐⭐

> **Yêu cầu tối quan trọng:** đề mới phải có **ĐỦ số section như đề gốc**, và **mỗi section hoàn chỉnh 100%**.
> Đề gốc 7 section ⇒ đề mới đúng 7 section, không thiếu, không dở. Section nào có vấn đề ⇒ **retry trong
> ngân sách cho phép**; vẫn không cứu được ⇒ **báo lỗi và DỪNG, không tạo đề nào** (không bao giờ để lại
> đề dở dang).

### 9.1 "Section hoàn chỉnh 100%" nghĩa là gì

Một section chỉ được tính **complete** khi qua **tất cả** các cửa:
- Qua **Tầng A** self-review (§7): không còn issue `critical`.
- Qua **Tầng B** validate cấu trúc (§8): materials + question_data + gap markers + structural-invariant.
- **Đủ số câu hỏi** như section gốc; mỗi câu có `question_data` hợp lệ đúng `question_type`.
- Media giữ URL gốc; mọi `{{gap:N}}` resolve hợp lệ.

### 9.2 Ngân sách retry mỗi section (rõ ràng, hữu hạn)

Mỗi section có "budget" cố định để đạt complete, **lồng 2 cấp**:
- **Trong 1 lần generate:** chạy Tầng A self-review tối đa `AI_SELF_REVIEW_ROUNDS` vòng (default 2).
- **Số lần generate:** nếu sau self-review vẫn còn `critical` (Tầng A chưa đạt) **hoặc** Tầng B (cấu trúc) lỗi ⇒ **re-generate** kèm lỗi cụ thể, tối đa **2** lần.

⇒ Tối đa **1 + 2 = 3 lần generate**/section, mỗi lần ≤ `AI_SELF_REVIEW_ROUNDS` vòng self-review. Cả 2 nhánh lỗi (self-review-fail / structural-fail) **dùng chung** bộ đếm re-generate. Hết budget mà chưa complete ⇒ section **FAILED** (không có cửa thoát "tạm chấp nhận").

### 9.3 Bảo đảm toàn đề (all-or-nothing)

1. **Sinh + hoàn chỉnh TẤT CẢ section trong bộ nhớ TRƯỚC**, chưa ghi DB gì.
2. **Dừng ở section FAILED đầu tiên** (fail-fast): không sinh tiếp các section còn lại, **không** gọi `create_exam_nested` ⇒ **không có đề rác**, tiết kiệm token.
3. Trước khi persist, **assert đếm khớp**: `len(generated_sections) == len(source_active_sections)`. Lệch ⇒ abort (lỗi logic).
4. `create_exam_nested` ghi trong **1 transaction**: lỗi DB giữa chừng ⇒ rollback toàn bộ (đề không tồn tại một phần).
5. **Sanity sau persist**: đối chiếu `created_counts.sections` == số section nguồn và `created_counts.questions` == tổng số câu nguồn; lệch ⇒ raise + log (bắt bug code), không trả về "thành công" sai.

> Lưu ý phạm vi "section nguồn": chỉ tính **section + question active** (`deleted_at IS NULL`) của đề gốc — section/câu đã soft-delete không nằm trong đề mới (giống cách `publish`/`create_exam_nested` đang đếm).

### 9.4 Báo lỗi & dừng

Khi abort, `GenerationReport`:
- `aborted_reason`: nêu rõ **section_position** + nguyên nhân (self-review issues `critical` còn lại / validation error cuối / đếm lệch).
- `sections_ok < sections_total`; trạng thái mỗi section: `ok` / `failed(reason)` (section FAILED) / `chưa chạy` (các section sau cái FAILED đầu tiên — fail-fast §9.3).
- Script **exit code ≠ 0**; **không tạo exam**.

### 9.5 Vì sao fail-fast (không "tạo phần nào hay phần đó") — Mode 1

Đề thi thiếu section / có section dở là **vô dụng và nguy hiểm** (học sinh làm phải đề lỗi). Thà không có đề mới còn hơn có đề không hoàn chỉnh ⇒ người vận hành chạy lại (đổi K, sửa admin prompt, hoặc đổi model) cho tới khi đạt.

### 9.6 Mode 2 (tương tác từng part) — completeness do FE/admin chịu trách nhiệm

All-or-nothing ở §9.1–9.5 **chỉ áp cho Mode 1** (server tự lắp + tự lưu). Ở **Mode 2**:
- Gen từng part là **độc lập**: part nào FAILED (hết budget §9.2) thì **chỉ part đó báo lỗi**, admin **gen lại riêng part đó** — không kéo cả cụm.
- Gen-hết-1-lần (preview) trả **per-part status** (`ok`/`failed`); part fail không chặn part khác.
- "Đủ part hay chưa" là **admin quyết** (cột phải). Khi bấm **Save**, `assemble_generated_exam` chỉ **validate cấu trúc từng part** (materials/question_data/gap) rồi `create_exam_nested`; **không ép** "đủ N part như đề gốc" (admin có thể lưu khác số part). Tuỳ chọn: **cảnh báo** nếu số part < số part active của đề gốc (FE hiển thị, không chặn).
- Đề lưu ra vẫn **draft** + mang provenance (`generated_from_exam_id`, `generation_meta`).

---

## 10. Hướng dẫn AI từ admin: per-type (config) + per-section (lúc gen) ⭐

> Admin "dạy" AI qua **2 nguồn** cùng tồn tại & **cộng dồn** — phục vụ **2 mục đích khác nhau**, không thay thế nhau:
> - **(A) Per-type — config bền = hướng dẫn NGHIỆP VỤ:** bảng `section_type_prompts`. Mô tả *quy ước/đặc thù của loại section đó* (vd dạng KET Part 2 dựng bảng ra sao, fill_blank chấp nhận biến thể nào). Áp cho **mọi** section cùng type, **mọi** lần gen.
> - **(B) Per-section — ad-hoc lúc generate = Ý TƯỞNG nội dung:** prompt admin nhập cho **từng section** ngay khi bấm gen, chỉ cho lần gen này (không lưu config). Mô tả *ý tưởng nội dung cụ thể* (vd "section 3 đổi sang chủ đề du lịch", "giữ giọng văn trang trọng"). *(Yêu cầu từ client.)*
>
> Hai cái **bổ trợ** nhau (nghiệp vụ ổn định + ý tưởng nhất thời), hiếm khi mâu thuẫn; nếu có, (B) — cụ thể hơn — thắng, nhưng cả hai vẫn dưới ràng buộc cấu trúc/đáp án (§10.4).

### 10.1 (A) Bảng `section_type_prompts` — config per-type, bền

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `type` | text **PRIMARY KEY** | 1 trong các section type hợp lệ (`multiple_choice`, `multiple_choice_shared`, `fill_blank`, `matching`, `writing`, `speaking`, **`form_completion`**). 1 row / type. |
| `additional_prompt` | text NOT NULL | Đoạn hướng dẫn bổ sung của admin cho type đó. |
| `updated_at` | timestamptz | Mốc sửa gần nhất. |
| `updated_by` | uuid → profiles | Ai sửa (audit). |

- Bảng **rỗng mặc định**; không có row cho type nào ⇒ không chèn gì.
- CRUD qua API admin (§14.6). Migration **0019** (xem §11). Ví dụ `multiple_choice_shared`: *"luôn dựng bảng kiểu KET Reading Part 2 — 1 nhóm option dùng chung cho nhiều câu"*.

> **`form_completion`** (thêm ở migration 0014 của dự án): là *rendering hint* cho dạng KET note/form completion — câu hỏi vẫn `question_type='fill_blank'` (cùng cách chấm string-match + strip), **không** có `{{gap:N}}`; layout form + `label`/`prefix`/`postfix` mỗi blank nằm trong `question_data` (presentation-only). Khi gen: AI giữ nguyên cấu trúc form (số blank, prefix/postfix), chỉ viết lại nội dung label + đáp án `correct_answers`.

### 10.2 (B) Per-section prompt tại lúc generate — ad-hoc ⭐ MỚI

- Khi admin bấm gen, FE cho nhập 1 prompt **riêng cho từng section** của đề gốc (tuỳ chọn — section nào muốn thì nhập).
- Truyền trong **body POST**, keyed theo **source `sectionId`** (bền hơn position): `sectionPrompts: { "<sourceSectionId>": "hướng dẫn riêng cho section này...", ... }`.
- Chỉ áp cho **đúng section đó, trong lần gen này**; **KHÔNG** lưu vào config — chỉ ghi vào job/`generation_meta` để audit/tái lập.
- Key không khớp section nào của đề gốc ⇒ **bỏ qua** (ghi log/report). Section không có entry ⇒ bỏ qua.
- Khác biệt cốt lõi với (A): **(A) theo loại & bền — (B) theo từng section cụ thể & nhất thời.**

### 10.3 Cách inject vào lúc gen (gộp A + B)

1. Load (A) `{type: prompt}` **1 lần**; nhận (B) `{sectionId: prompt}` từ request.
2. `prompts.build(section, k, type_prompt, section_prompt)` chèn **2 block có nhãn rõ**, đặt **sau** bất biến (§4) + K-instruction (§3):
   - `### ADMIN GUIDANCE FOR THIS SECTION TYPE` ← (A)
   - `### ADMIN GUIDANCE FOR THIS SPECIFIC SECTION` ← (B)
3. `verify_section` (§7) **cũng nhận cả hai** để giám khảo hiểu ý đồ admin.
4. Thiếu (A) và/hoặc (B) ⇒ bỏ qua phần thiếu.

### 10.4 Thứ tự ưu tiên (BẮT BUỘC ghi rõ trong system prompt)

Cả (A) và (B) **chỉ là hướng dẫn bổ sung (preference)**, KHÔNG override:

```
1. Bất biến cấu trúc (§4)  +  2. Quy tắc chất lượng / self-review (§7)   ← cao nhất, không thể phá
3. K-instruction (§3)
4. Admin per-type prompt (A)
5. Admin per-section prompt (B)   ← cụ thể hơn (A): khi (A) và (B) mâu thuẫn, (B) thắng — nhưng vẫn DƯỚI invariant
```

Mâu thuẫn invariant/đúng-đáp-án ⇒ **invariant thắng**. System prompt nêu rõ: không guidance nào được làm sai cấu trúc, số câu, hay tính đúng của đáp án.

### 10.5 (tuỳ chọn, mở rộng sau) global prompt

Có thể thêm row đặc biệt `type = '*'` áp cho **mọi** section (xếp trước per-type prompt). v1 chỉ làm **exact-match per-type**; global để ngỏ.

---

## 11. DB — migrations (additive, idempotent)

> Migration mới nhất của dự án hiện là **0017** (`attempt_highlights`). 3 migration của feature này dùng **số kế tiếp 0018/0019/0020**. Section type `form_completion` đã được thêm ở **0014** của dự án (`question_type='fill_blank'` + presentation fields trong `question_data`).

**Migration 0018** — truy vết đề sinh ra:
```sql
ALTER TABLE public.exams
  ADD COLUMN IF NOT EXISTS generated_from_exam_id uuid REFERENCES public.exams(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS generation_meta jsonb;
```

**Migration 0019** — bảng prompt config theo type (§10):
```sql
CREATE TABLE IF NOT EXISTS public.section_type_prompts (
    type              text PRIMARY KEY
                        CHECK (type IN ('multiple_choice','multiple_choice_shared',
                                        'fill_blank','matching','writing','speaking',
                                        'form_completion')),
    additional_prompt text NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL
);
-- RLS bật, ghi giới hạn service_role (giống các bảng khác); đọc qua service.
```

**Migration 0020** — bảng job cho async gen (§14, Phase 2):
```sql
CREATE TABLE IF NOT EXISTS public.exam_generation_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope             text NOT NULL DEFAULT 'exam'
                        CHECK (scope IN ('exam','section','exam_preview')),  -- Mode 1 / Mode 2 single / Mode 2 all
    source_exam_id    uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    target_section_id uuid REFERENCES public.sections(id) ON DELETE CASCADE, -- chỉ scope='section'
    k                 integer NOT NULL CHECK (k BETWEEN 1 AND 5),
    title             text,
    status            text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','succeeded','failed','aborted')),
    sections_total    integer,
    sections_done     integer NOT NULL DEFAULT 0,
    current_section   integer,
    result_exam_id    uuid REFERENCES public.exams(id) ON DELETE SET NULL,   -- chỉ scope='exam' (Mode 1 tự lưu)
    report            jsonb,        -- scope='section'/'exam_preview': report.sections[] chứa section payload đã gen (KHÔNG lưu đề)
    aborted_reason    text,
    cancel_requested  boolean NOT NULL DEFAULT false,
    created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);
CREATE INDEX IF NOT EXISTS exam_generation_jobs_status_idx ON public.exam_generation_jobs (status, created_at DESC);
-- RLS bật; chỉ service_role ghi/đọc (API admin-scoped đọc qua service).
```

`generation_meta` shape (**audit thuần** — transcript đã dời vào `material.meta`, §5):
```jsonc
{
  "source_exam_id": "...",
  "k": 3,
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "generated_at": "2026-06-03T...Z",
  "section_prompts": { "<sourceSectionId>": "per-section prompt admin đã nhập (B, §10.2)" },
  "media_todos": [ { "section_position": 1, "material_index": 0, "media_type": "audio", "reason": "..." } ],
  "self_review": { "1": { "rounds": 1, "final_issues": [] }, "2": { "rounds": 2, "final_issues": [ { "severity": "minor", "problem": "..." } ] } },
  "token_usage": { "input": 12345, "output": 6789 },
  "retries": { "2": 1 }
}
```
`generated_from_exam_id` cho phép truy ngược đề nguồn. Cả hai field **nullable** ⇒ đề tạo tay không ảnh hưởng. `media_todos` là **snapshot tiện tra**; nguồn-sự-thật của "media cần thay" là `material.meta.pendingReplacement` (§5).

### 11.1 Thay đổi shape `materials` jsonb (KHÔNG thêm cột) — §5

`materials` đã là jsonb → thêm `meta` vào block `audio`/`image` (optional):
```jsonc
audio:  { type, url, label, "meta": { "transcript": str?, "pendingReplacement": bool } }
image:  { type, url, alt, label, "meta": { "description": str?, "pendingReplacement": bool } }
```
Kéo theo (ngoài exam-ai-generation, chạm code chung):
- **`section_service._validate_materials`**: mở rộng discriminated union để chấp nhận `meta` (optional) trên audio/image; `text` không có meta.
- **Serializer**: **strip `material.meta`** khỏi payload phía student (exam published + attempt start/active/detail). Admin (soạn đề) giữ nguyên. Cân nhắc thêm helper `strip_material_meta` cạnh `strip_correct` (utils/grading_utils.py).
- **Backfill**: audio/image cũ không có `meta` ⇒ đề đó **chưa đủ điều kiện làm đề gốc** (§5.3) cho tới khi admin điền meta; không cần migration dữ liệu.

---

## 12. Config & adapter

> **Đa provider qua adapter** (memory `ai-via-openrouter`). 4 provider: `openrouter` (default,
> gateway nhiều model qua slug), `groq` & `gemini` (direct, OpenAI-compatible), `anthropic`
> (SDK riêng). 3 cái OpenAI-compatible chia chung 1 base class.

### 12.1 Resolution — per-request > DB config > env (CẬP NHẬT 2026-06-10)
`provider` / `model` / `max_tokens` / `self_review_rounds` được resolve theo thứ tự:
1. **Per-request override** — FE gửi `aiModel`/`aiProvider` trong request gen (§14).
2. **DB config** — bảng singleton `ai_generation_settings` (migration 0022), admin sửa qua
   `GET/PUT /api/admin/ai-settings` **không cần redeploy**. Mỗi field NULL = dùng env.
3. **Env default** — `config/settings.py` (dưới đây).

→ `services/ai_settings_service.get_effective()` lo bước 2–3 (chịu lỗi bảng-chưa-migrate → env).

### 12.2 Env defaults (`config/settings.py`)
| Biến | Default | Ý nghĩa |
|---|---|---|
| `AI_PROVIDER` | `openrouter` | `openrouter` \| `groq` \| `gemini` \| `anthropic`. |
| `AI_MODEL` | `anthropic/claude-sonnet-4.5` | id theo provider (slug OpenRouter; hoặc `llama-3.3-70b-versatile`, `gemini-3-flash-preview`, `claude-sonnet-4-6`). |
| `AI_MAX_TOKENS` | `8000` | Cap output/section (là *reservation* tính vào TPM/credit — để vừa phải, vd 4000). |
| `AI_SELF_REVIEW_ROUNDS` | `2` | Số vòng self-review/section (§7); 0 = tắt (vd cho Gemini flash verify chập chờn). |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | / `…/api/v1` | provider=openrouter (dùng chung text+image). |
| `GROQ_API_KEY` / `GROQ_BASE_URL` | / `api.groq.com/openai/v1` | provider=groq. |
| `GEMINI_API_KEY` / `GEMINI_BASE_URL` | / `…/v1beta/openai/` | provider=gemini. |
| `ANTHROPIC_API_KEY` | `None` | provider=anthropic. |

`requirements.txt`: `openai` (client OpenAI-compatible) + `anthropic`.

### 12.3 Adapters (`services/ai/`)
```python
def get_ai_generator(*, provider=None, model=None, max_tokens=None) -> AIContentGenerator
```
- `adapters/openai_compatible.py` — **base** `OpenAICompatibleGenerator`: `openai` SDK + OpenAI
  tool-calling (convert schema Anthropic→`function`), usage, optional `extra_create`.
- `openrouter_generator.py` / `groq_generator.py` / `gemini_generator.py` — subclass mỏng (chỉ
  khác key/base_url). Gemini đặt `reasoning_effort=none` để thinking không phá function-call
  (`gemini-3-flash-preview` chạy; 2.5-flash bị `MALFORMED_FUNCTION_CALL`).
- `anthropic_generator.py` (option) — `AsyncAnthropic`, tool-use forced + prompt caching.
- Adapter phơi `.model`/`.provider` → ghi vào `generation_meta` (model THẬT đã dùng).

---

## 13. Script CLI (entry-point background)

`scripts/generate_similar_exam.py` — mirror `scripts/init_schema.py`:
- Load `.env`, init db pool.
- Args: `--source <exam_id>` (required), `--k <1..5>` (required), `--title <str>` (optional, default `"{gốc} (AI K{k})"`), `--created-by <uuid>` (optional), `--section-prompts <path.json>` (optional — map `{sectionId: prompt}`, B §10.2), `--dry-run` (sinh + validate + in report, KHÔNG ghi DB).
- **Precondition §5.3** (meta đủ trên mọi audio/image của đề gốc) được check **đầu tiên**; thiếu ⇒ in lỗi + exit ≠0, không gen.
- Gọi `exam_generation_service.generate_similar_exam(...)`, in **GenerationReport** ra console (UTF-8), exit code ≠0 nếu abort.

`GenerationReport` (dataclass): `new_exam_id`, `sections_total`, `sections_ok`, `self_review{}` (số vòng + issues cuối/section), `media_todos[]` (suy ra từ `pendingReplacement`; transcript thực nằm trong `material.meta`), `retries{}`, `token_usage`, `aborted_reason?`.

---

## 14. HTTP API + async job — Phase 2 (FE-facing)

> Mở engine §1–13 cho Frontend. Tất cả endpoint **`require_admin`**. Mọi tác vụ gen chạy **nền** bằng
> FastAPI BackgroundTasks; FE **poll** job. Hỗ trợ **2 mode** (§2.2): **Mode 1** (cả đề, tự lưu) +
> **Mode 2** (tương tác từng part, FE staging, admin tự Save). Cả 2 dùng chung lõi `generate_one_section`.

### 14.1 Execution model (BackgroundTasks + job row)

1. `POST` (1 trong 3 endpoint gen ở §14.2) → validate (§14.4) → tạo row `exam_generation_jobs` (đúng `scope`) status=`pending` → đẩy `BackgroundTasks` → trả **202** `{jobId, status:"pending"}` ngay.
2. Background task: set `running`, `sections_total` = số section sẽ gen (`scope=exam`/`exam_preview`: số section active đề gốc; `scope=section`: 1) → chạy hàm theo scope (`generate_similar_exam` / `generate_sections_preview` / `generate_one_section`) với **callback progress** cập nhật `sections_done`/`current_section` (1 UPDATE nhỏ/section).
3. Kết thúc: `succeeded` (+ `result_exam_id` + `report`) **hoặc** `failed`/`aborted` (+ `aborted_reason`). `finished_at = now()`.
4. FE poll `GET .../{jobId}` mỗi ~2–3s tới khi status terminal.

**Hạn chế đã biết (chấp nhận v1):** BackgroundTasks chạy in-process → **restart giữa chừng ⇒ job kẹt `running`**. Mitigations: `updated_at` cập nhật mỗi section; job `running` mà `updated_at` quá ngưỡng (vd >15') coi là **stale** → FE cảnh báo, admin gen lại. (Không auto-resume ở v1.)

### 14.2 Endpoints — Generation

**Tạo job (đều trả 202 `{jobId, status}`, chạy nền, poll qua GET):**

| Method | Path | Mode | Vai trò |
|---|---|---|---|
| `POST` | `/api/admin/exam-generations` | 1 | Body `{sourceExamId, k, title?, sectionPrompts?, aiModel?, aiProvider?}` → job `scope=exam` → **tự lưu** đề draft (all-or-nothing). |
| `POST` | `/api/admin/exam-generations/section` | 2 | Body `{sourceSectionId, k, sectionPrompt?, aiModel?, aiProvider?}` → job `scope=section` → trả **1 section payload** trong `report.sections[0]` (**KHÔNG lưu**). |
| `POST` | `/api/admin/exam-generations/preview` | 2 | Body `{sourceExamId, k, sectionPrompts?, aiModel?, aiProvider?}` → job `scope=exam_preview` → trả **N section payload** + per-part status (**KHÔNG lưu**). |

> `title` bỏ trống (Mode 1 + Save §14.5) ⇒ mặc định `"{tên đề gốc} (AI K{k})"` (giống script §13).
> **`aiModel`/`aiProvider`** (tuỳ chọn, §12.1): override model/provider **cho riêng request này** (else DB config → env). `aiProvider` lạ ⇒ 400.

**Poll / quản lý + chọn model (chung mọi scope):**

| Method | Path | Vai trò |
|---|---|---|
| `GET` | `/api/admin/exam-generations/{jobId}` | Trạng thái + progress + `report` (+ `resultExamId` nếu scope=exam). FE poll. |
| `GET` | `/api/admin/exam-generations` | List job (phân trang, lọc `status`/`scope`) cho Job center. |
| `GET` | `/api/admin/exam-generations/models?provider=` | List model id của provider (cho dropdown FE). Mặc định provider hiện hành. |
| `POST` | `/api/admin/exam-generations/{jobId}/cancel` | `cancel_requested=true` (cooperative — §14.3). |

**AI settings (config mặc định, không cần redeploy — §12.1):**

| Method | Path | Vai trò |
|---|---|---|
| `GET` | `/api/admin/ai-settings` | `{effective, stored}` — config đang dùng + override thô (null=env). |
| `PUT` | `/api/admin/ai-settings` | Set subset `{provider?, model?, maxTokens?, selfReviewRounds?}`; null để xoá field (→ env); provider lạ ⇒ 400. |

**Save (Mode 2, đồng bộ — KHÔNG qua job, chỉ ghi DB):** xem §14.5.

Job status response (camelCase):
```jsonc
{ "jobId":"...", "scope":"exam", "status":"running", "sourceExamId":"...", "k":3,
  "sectionsTotal":7, "sectionsDone":4, "currentSection":5,
  "resultExamId":null, "report":null, "abortedReason":null,
  "createdAt":"...", "updatedAt":"...", "finishedAt":null }
```
- `scope=exam` succeeded → `resultExamId` + `report` (GenerationReport).
- `scope=section`/`exam_preview` succeeded → `report.sections[]` = (các) section payload đã gen + per-part status; **không** có `resultExamId`. FE đọc `report.sections` để render "cột phải" và **giữ ở client**.

### 14.3 Cancel (cooperative)

Cancel **không giết giữa 1 lời gọi AI**. Service kiểm tra `cancel_requested` **giữa các section**; nếu bật ⇒ dừng sạch, status=`aborted`, `aborted_reason="cancelled by admin"`. Mode 1: không ghi đề (all-or-nothing §9). Mode 2 preview: trả các part đã xong trong report. Cancel sau khi `succeeded`/`failed` ⇒ no-op.

### 14.4 Validation & RBAC (POST các endpoint gen)

- `require_admin` cho **mọi** endpoint nhóm này.
- **Mode 1 / preview** (`sourceExamId`): tồn tại + `deleted_at IS NULL` + có **≥1 active question** (404/400). **Meta precondition (§5.3)** trên **toàn bộ** đề gốc.
- **Mode 2 single** (`sourceSectionId`): section tồn tại + active + thuộc 1 đề chưa xoá (404). **Meta precondition (§5.3)** chỉ trên **section đó** (audio→transcript, image→description); thiếu ⇒ 400.
- `sectionPrompts`/`sectionPrompt` (optional, §10.2): key lạ ⇒ **bỏ qua + ghi report** (không fail).
- `k`: integer ∈ [1,5] → 422 nếu sai.
- Cho phép **gen nhiều lần** từ cùng 1 đề gốc / part (mỗi job độc lập).

### 14.5 Save đề đã lắp (Mode 2) — đồng bộ

`POST /api/admin/exam-generations/assembled-exam` — body `{sourceExamId, title?, sections:[<section payload đã gen từ cột phải>]}`:
- **KHÔNG gọi AI** (chỉ ghi DB) ⇒ đồng bộ, trả về đề mới ngay.
- Validate **từng section** (materials/question_data/gap, §8) — chống FE gửi shape hỏng.
- Gọi `create_exam_nested` (1 transaction) → đề **draft** (`is_published=false`) + set `generated_from_exam_id=sourceExamId` + `generation_meta` (tổng hợp provenance từ các part: k, model, section_prompts, self_review, media_todos suy từ `pendingReplacement`).
- **Không ép** "đủ N part" (§9.6); tuỳ chọn trả `warning` nếu số part < số part active đề gốc.
- `require_admin`.

### 14.6 Endpoints — Section-type prompts (CRUD, §10)

| Method | Path | Vai trò |
|---|---|---|
| `GET` | `/api/admin/section-type-prompts` | List tất cả `{type, additionalPrompt, updatedAt, updatedBy}`. |
| `PUT` | `/api/admin/section-type-prompts/{type}` | Upsert `additionalPrompt` cho 1 type (validate `type` ∈ allowed; set `updated_by`/`updated_at`). |
| `DELETE` | `/api/admin/section-type-prompts/{type}` | Xoá prompt của type (404 nếu chưa có). |

### 14.7 Layout code (theo pattern hiện có)

```
api/exam_generations/   __init__ + routes.py (admin_router /api/admin/exam-generations) + schemas.py
api/section_type_prompts/ (hoặc gộp vào api/admin) routes.py + schemas.py
services/exam_generation_service.py
   • generate_one_section(...)            ← LÕI dùng chung (§2.1)
   • generate_similar_exam(..., progress_cb)   Mode 1 (loop lõi + all-or-nothing + create_exam_nested)
   • generate_sections_preview(..., progress_cb) Mode 2 preview (loop lõi, per-part status, KHÔNG lưu)
   • assemble_generated_exam(...)         Mode 2 Save (validate sections[] + create_exam_nested + provenance)
services/generation_job_service.py    create_job(scope,...)/get/list/mark_running/update_progress/finish/request_cancel
services/section_type_prompt_service.py  list/upsert/delete + load_map() (dùng khi gen)
```
Review/publish đề draft: **dùng lại** endpoint exam có sẵn — không thêm.

---

## 15. Bảng Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|---|---|---|---|
| 1 | Granularity gọi AI | **Per-section** (1 call/section) | Cả đề trong 1 call | Theo yêu cầu; prompt gọn, dễ validate + retry từng phần, ít lỗi shape. |
| 2 | Thực thi (engine) | **Script background async** (Phase 1) | HTTP sync trực tiếp | Tránh timeout Render free; engine chạy độc lập FE. Phase 2 bọc API quanh chính engine này. |
| 3 | Provider/model | **OpenRouter mặc định (1 key, nhiều model qua slug `AI_MODEL`); anthropic là option** | Hard-code model / khoá 1 provider | Đổi model/provider không sửa code; OpenRouter cho linh hoạt nhiều model (memory `ai-via-openrouter`). |
| 4 | Ép output | **Tool-use structured output** | Parse JSON từ text tự do | Giảm lỗi shape; vẫn validate lại phía ta. |
| 5 | Đảm bảo chất lượng | **AI self-review 1–2 vòng (giám khảo độc lập) + draft** (§7) | Tin output 1 lần / chỉ validate cấu trúc | Đề thi thật cần material↔question nhất quán + đáp án đúng; validator code không soi được ngữ nghĩa. Đây là yêu cầu cốt lõi. |
| 6 | Media meta | **`meta` trên material (audio→transcript, image→description, +pendingReplacement), dùng 2 chiều input↔output, strip khỏi payload student** (§5) | Lưu transcript ở exam-level keyed-position / nhét vào material không strip / gửi file cho AI | Đi theo bộ với material (bền khi sửa đề); transcript là *nguyên liệu* cho AI (hiệu quả hơn gửi file) + *sản phẩm* cho feature tạo audio sau; không thêm cột; nhưng phải strip vì = đáp án nghe. |
| 6b | Đề gốc thiếu meta | **Precondition cứng: từ chối làm đề gốc (400) tới khi đủ meta** (§5.3) | Copy verbatim section thiếu meta / abort giữa chừng | Đề mới phải vary **mọi** section (không lẫn section y hệt gốc); ép dữ liệu nguồn sạch; chặn sớm không tốn token. |
| 7 | Ghi DB | **Validate hết rồi 1 transaction** | Ghi dần từng section | Fail-fast, không tạo đề rác (đúng pattern `create_exam_nested`). |
| 8 | Truy vết | **migration 0018 (`generated_from_exam_id`+`generation_meta`)** | Chỉ ghi file report | Tra được từ DB; lưu cờ media + self-review ngay trên exam. Additive, an toàn. |
| 9 | Đề mới | **Luôn `is_published=false`** | Tự publish | Bắt buộc người review trước khi học sinh thấy. |
| 10 | Hoàn chỉnh toàn đề | **All-or-nothing: đủ N section, mỗi section complete, lỗi không cứu được ⇒ abort, không tạo gì** (§9) | Tạo phần nào hay phần đó / để section dở | Đề thiếu section hoặc có section lỗi là vô dụng & nguy hiểm cho học sinh; thà không có còn hơn có đề hỏng. |
| 11 | Tuỳ biến theo type | **Bảng `section_type_prompts` (type↔additional_prompt), inject lúc gen, ưu tiên thấp hơn invariant** (§10) | Hard-code hướng dẫn / không tuỳ biến | Admin "dạy" AI ý đồ riêng từng loại section mà không sửa code; vẫn không phá được cấu trúc/đáp án. |
| 11b | Prompt per-section lúc gen | **`sectionPrompts` ad-hoc trong request, keyed theo sourceSectionId, không lưu config, ưu tiên trên (A) dưới invariant** (§10.2) | Chỉ có config per-type / nhét chung K-instruction | Client cần "dặn" riêng từng section ngay khi gen; nhất thời nên không vào config; key theo id để bền với reorder. |
| 12 | FE thực thi (Phase 2) | **API async: BackgroundTasks + bảng `exam_generation_jobs` + poll** (§14) | HTTP sync (timeout) / worker+queue riêng (nặng) | Gen lâu vài phút; BackgroundTasks đúng pattern email hiện có, không thêm hạ tầng. Đổi lại: restart mất job (chấp nhận v1). |
| 13 | RBAC gen | **Chỉ `require_admin`** | Teacher + admin | Kiểm soát chi phí AI; teacher chưa tự gen ở v1. |
| 14 | Hủy job | **Cooperative giữa các section** (§14.3) | Kill cứng giữa call AI | Đơn giản, an toàn; Mode 1 hủy ⇒ không tạo đề. |
| 15 | Lõi tái sử dụng | **Tách `generate_one_section` làm lõi chung cho cả 2 mode** (§2.1) | Copy logic gen part ở mỗi mode | 1 chỗ chứa pipeline gen+self-review+validate; Mode 1/2 chỉ khác cách *loop* và *lưu*. |
| 16 | Mode 2 (từng part) | **Job per-part (`scope=section`/`exam_preview`) trả section payload, KHÔNG lưu; admin tự Save (§14.5)** | Bắt buộc cả đề / server-side staging session | Client cần xem/chỉnh/gen lại từng part ở "cột phải" rồi mới lưu; per-part không all-or-nothing (§9.6). |
| 17 | Staging Mode 2 | **Client giữ part, Save gửi cả cụm → `assemble_generated_exam`** | Server lưu staging session (thêm bảng) | Đơn giản, không thêm bảng; FE đã giữ "cột phải"; reload mất nháp — chấp nhận. |

---

## 16. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| `correct_index`/đáp án sai sau khi viết lại | **AI self-review 1–2 vòng (§7)** bắt lỗi đáp án + tự sửa; draft + giáo viên review cuối |
| Câu hỏi không trả lời được từ material | self-review checklist "material↔question coherence" → tự sửa hoặc abort |
| Gap `{{gap:N}}` lệch | `validate_gap_markers` + structural checker → retry |
| Audio/ảnh cũ không khớp câu mới | `material.meta.pendingReplacement=true` + `meta.transcript`/`description` mới (§5); đề ở draft tới khi thay |
| Đề gốc thiếu transcript/description ⇒ AI "mù" media | **Precondition §5.3: từ chối ngay (400)** — không cho gen tới khi admin điền meta; không tốn token |
| `material.meta` (transcript = đáp án nghe) lộ cho học sinh | **strip `material.meta`** khỏi payload student (§11.1) — bắt buộc |
| AI phá số option/số câu | structural-invariant checker → retry → abort |
| Self-review không hội tụ (vẫn còn `critical`) | hết vòng ⇒ coi là lỗi cứng ⇒ re-generate/abort, ghi issues vào report |
| Đề mới thiếu section / có section dở | **all-or-nothing (§9)**: 1 section FAILED ⇒ dừng + không tạo đề; assert đếm section khớp trước & sau persist |
| Job kẹt `running` khi restart process (BackgroundTasks) | `updated_at` mỗi section; job quá ngưỡng coi là **stale** → FE cảnh báo, admin gen lại; v1 không auto-resume (§14.1) |
| Admin gen trùng nhiều job tốn token | mỗi job độc lập (cho phép có chủ đích); Job center hiển thị token_usage để admin tự kiểm soát |
| Admin prompt (A/B) mâu thuẫn invariant / làm sai đáp án | thứ tự ưu tiên §10.4 (invariant > A > B đều dưới invariant) nêu rõ trong system prompt; self-review (§7) + validator (§8) vẫn chặn |
| Tốn token (thêm vòng verify) | log token/section vào report; prompt caching system prompt; `AI_SELF_REVIEW_ROUNDS` chỉnh được |
| Lỗi giữa chừng | validate toàn bộ trước, ghi 1 transaction |
| Thiếu API key | adapter raise RuntimeError sớm (precondition) |
