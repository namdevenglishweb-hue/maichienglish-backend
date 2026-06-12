---
feature: exam-gen-v3-spec-mode
type: design
status: approved
last-updated: 2026-06-12
author: backend
depends-on: [exam-ai-generation]
changelog: APPROVED 2026-06-12 — v2 draft (2 vòng review: B1/B2/B3/B4-K, M2-M9,
  N1-N13, REJECT M6, type-core) + 7 sửa làm-rõ từ vòng verify lượt 3 (F1-F7)
---

# Exam Generation v3 — Spec Mode (Design / Core feature)

> **Một dòng:** ở K≥3, **text đề gốc không bao giờ vào prompt generate/verify** — thay
> bằng skill map trừu tượng + topic + diversity seed do server random; chống đạo văn
> từ gốc rễ thay vì thuyết phục model.
>
> **Nguồn:** port từ amendment đã APPROVED của client
> (`maichienglish-feature-ai-exam-generation/exam-ai-generation-design-AMENDMENT.md`
> v1.1, harness 8/8, trigram overlap thực đo 0.0–0.6%). Kiến trúc nền của ta (2 tầng
> kiểm tra, all-or-nothing, invariant checker, merge re-impose, job system, registry
> PROMPT_VERSIONS) **GIỮ NGUYÊN** — v3 chỉ thay tầng prompt/payload.

---

## 1. Vấn đề v1/v2 chưa giải được

v2 (verify thấy source + DO NOT CLONE, hiện là default) chỉ *chống lại* lực anchor
chứ không loại bỏ nó: đề gốc vẫn nằm nguyên văn trong prompt generate ở mọi K →
model anchor → variance cao (A/B đo được: max-overlap dao động 0.11–0.79 giữa các
lần chạy). Client tái hiện bug và chứng minh: **giấu text gốc khỏi prompt ⇒ overlap
rơi về 0.0–0.6% ổn định**, skill fidelity K=3 đạt 5/5 câu.

## 2. Kiến trúc: ORCHESTRATION chung + TYPE-CORE cắm được ⭐ (chốt 2026-06-12)

v3 tách làm 2 lớp:

```
┌─ ORCHESTRATION (type-agnostic — port trung thành pipeline client) ──────────┐
│  cache → ANALYZE → leak check → topic+seed → GENERATE → trigram guard       │
│  → VERIFY (structure-only) → shuffle → Tầng B → budget/retry                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ nhận tham số  core: str
┌─ TYPE-CORE (cắm theo loại câu) ──────────────────────────────────────────────┐
│  mỗi core định nghĩa: eligibility · skill_map schema (phần per-type) ·       │
│  output contract (shape emit) · "structure" nghĩa là gì (facts derive từ     │
│  source) · wording block per-type trong prompt                               │
│  Đợt này: CHỈ core="multiple_choice". Seam để sẵn cho shared_option /        │
│  fill_blank / form_completion (client nghiên cứu tiếp, implement sau).       │
│  ⚠️ Scope thật của seam (F4): các bước 8–10 của orchestration (word-count    │
│  giả định 1 text material; shuffle; trigram trên material) hiện MC-bound —   │
│  thêm core mới sẽ phải mở hook per-core ở các bước đó, KHÔNG chỉ viết core.  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Contract của biến `core`:** bộ xử lý spec-mode nhận `core` (vd
`core="multiple_choice"`) như một **tham số đầu vào** xác định lần chạy dùng
core nào — engine **KHÔNG tự route** bên trong. Việc *gán* `core` (auto theo
`question_type` của section, hay manual sau này) là concern **bên ngoài** engine.

**Ranh giới cụ thể (F1):** eligibility tách 2 tầng —
(a) **orchestration gate** (điều kiện 1–2 §3: K≥3, level KET/PET) thuộc lớp
orchestration, dùng chung mọi core; (b) **core.eligibility** (điều kiện 3–6 §3:
MC thuần, 1 text material, option đồng nhất, text-only) do từng type-core định
nghĩa. `generate_one_section` là **host của lớp gán mỏng**: chạy gate → hỏi
core.eligibility → đạt ⇒ gọi spec engine với `core="multiple_choice"`; không đạt
⇒ không gọi spec engine (fallback rewrite). Core fill_blank sau này chỉ thay (b),
không duplicate (a).

## 3. Scope & eligibility đợt này (chốt B2 + 2 vòng review)

Spec mode chạy khi **TẤT CẢ** điều kiện sau đúng (else → fallback **rewrite mode**
= hành vi v2, cho riêng section đó, không chặn cả đề; report ghi
`mode: "spec" | "rewrite"`):

1. `K ≥ 3` (K=1–2 luôn rewrite).
2. `exam.level ∈ {KET, PET}` (map KET→A2, PET→B1; IELTS/primary/secondary → rewrite
   — pool topic chỉ có A2/B1, để IELTS rơi vào A2 là tự mâu thuẫn với cefr trong spec).
3. Section type `multiple_choice` THUẦN (`multiple_choice_shared` LÙI đợt sau).
4. **Đúng 1 material, type `text`** (0 material — MC ngữ pháp stem-only — hoặc ≥2
   material → rewrite; spec schema chỉ mô tả được 1 passage).
5. **Số option đồng nhất mọi câu** (trộn 3/4 option → rewrite; spec chỉ mang 1 số
   `options_per_question`).
6. **Mọi option của mọi câu có `text` non-empty và KHÔNG có `image_url`**
   (picture-MC → rewrite; spec mode không sinh được URL ảnh).

Eligibility đánh giá **bằng pure function ở đầu `generate_one_section`, TRƯỚC khi
build payload** — kết quả quyết định cả nhánh đi lẫn `report.mode`, test được độc lập.

## 4. Pipeline spec mode (per-section, bên trong `generate_one_section`)

```
0. ELIGIBILITY (§3) → không đạt: rewrite mode (v2), DỪNG ở đây
1. CACHE LOOKUP section_skill_maps theo source_hash
   miss/stale → 2. ANALYZE (AI, tool emit_skill_map, temperature 0.2, prompt §10.1)
              → 3. LEAK CHECK (code, §6) — dính → re-run ANALYZE kèm danh sách từ
                   bị lộ, tối đa 2 lần → vẫn dính ⇒
                   SectionGenerationError("ANALYZE_DOMAIN_LEAK")
              → UPSERT cache
4. STRUCTURE FACTS derive BẰNG CODE từ source (KHÔNG tin ANALYZE đếm):
   num_questions, options_per_question, num_materials(=1), points — ghi đè lên
   skill_map.structure trước khi dùng (ANALYZE chỉ được tin phần định tính:
   text_genre, word_count_range, cefr_level, per_question, style_notes)
5. TOPIC + DIVERSITY SEED (code, rng):
   sectionPrompts[sid] có → topic = admin prompt (thắng random), subject hint generic
   không có → random TOPIC_POOL[level] + seed 5 chiều (narrator/role/setting/
              complication/specific_subject từ mini-pool theo topic)
6. GENERATE spec-prompt (§10.2): K=3 structure+per_question+style | K=4 structure+
   style | K=5 structure — prompt KHÔNG chứa text gốc; exam_context CHỈ level+skill
   (CẤM title/description — title đề gốc thường chứa chính topic cần giấu);
   model PHẢI tự sinh part_label + instructions + material content
7. MERGE: spec mode KHÔNG fallback content/instructions từ source — thiếu ⇒ fail
   như StructureMismatch (fallback = splice source vào đề xuất bản)
8. WORD-COUNT CHECK (code): len(material words) trong word_count_range ±15% slack
   → lệch ⇒ retry (slack vì range do ANALYZE ước lượng)
9. SHUFFLE answer keys (Nhịp 1, đã có)
10. TRIGRAM GUARD (code, TRƯỚC verify — mỗi lần fail tiết kiệm 2 call AI):
    so **content của text material mới ↔ content của text material gốc** (F3 —
    không gồm stem/option; đúng client); overlap > 10% VÀ ≥ 3 trigram chung ⇒
    retry (điều kiện ≥3 chống false-positive trên material ngắn: notice 30 từ
    chỉ có ~26 trigram)
11. VERIFY theo spec (§10.3): giám khảo nhận STRUCTURE spec + generated — KHÔNG
    source, KHÔNG per_question (kể cả K=3 — đúng client; xem decision #14).
    **Cơ chế fixed_section (F2 — đây là FLOW MỚI viết cho spec mode, không phải
    _self_review hiện có):** verify trả critical + fixed_section ⇒ merge strict
    (§4.7) ⇒ **re-check lại bước 8–10** (word-count, shuffle, trigram) trên bản
    fixed ⇒ PASS: chấp nhận **không re-verify** (client parity) — break; FAIL
    (hoặc không có fixed_section): coi như lỗi cứng ⇒ sang vòng GENERATE kế
    (re-roll seed), trong budget 1+2. Lặp verify tối đa AI_SELF_REVIEW_ROUNDS
    vòng cho mỗi lần generate. Lưu ý implementer: shuffle ở spec mode chạy
    TRƯỚC verify (khác thứ tự rewrite mode hiện hành) — code mới, không tái
    dùng nguyên _self_review.
12. Tầng B validation + invariant checker (KHÔNG đổi) → return
```

**Budget:** ANALYZE có budget riêng (1 + 2 re-run khi leak, chỉ chạy khi cache
miss); GENERATE giữ budget 1+2 hiện hữu — **mỗi lần re-generate RE-ROLL diversity
seed** (giữ topic nếu admin chỉ định; 4/5 chiều seed độc lập topic nên re-roll vẫn
hiệu quả) — seed được log là **seed của vòng THÀNH CÔNG**; verify rounds =
`AI_SELF_REVIEW_ROUNDS` như cũ.

**Vệ sinh retry_error (bắt buộc):** message lỗi ở spec mode chỉ được chứa
số/label/loại lỗi (vd "trigram overlap 14.2%", "word count 412 outside 280–360"),
**không bao giờ quote text** — retry_error được render vào prompt kế tiếp, quote
source là tự đục thủng invariant.

**Mode 1 all-or-nothing:** 1 section `ANALYZE_DOMAIN_LEAK` (hay fail budget) ⇒
abort cả đề như mọi SectionGenerationError — hành vi chấp nhận, nhất quán triết lý
hiện hữu; thử nghiệm/đề gốc lạ nên dùng Mode 2 (per-part).

## 5. INVARIANT "source không vào prompt" — cơ chế enforce

Text gốc chỉ được xuất hiện trong call **ANALYZE**. Cơ chế (đã sửa theo plumbing
thật — adapter pass nguyên payload vào render):

- Spec render nhận **bản payload đã scrub** (không có `section`, exam_context chỉ
  còn level+skill); `payload["section"]` vẫn tồn tại server-side cho merge/guard/
  overlap — chỉ render bị cấm dùng.
- **Assert ở đầu ra render** (sau khi đã ghép retry_block + admin blocks): chuỗi
  prompt không chứa nội dung text gốc — đây là test DoD #1, chạy cả nhánh retry.
- Đường rò đã nhận diện + xử lý: `exam_context.title` (cấm — §4.6), `retry_error`
  (vệ sinh — §4), skill_map (leak check lexical + prompt rule — §6), merge fallback
  (bỏ — §4.7). **Ngoài scope:** admin tự paste nguyên văn đề gốc vào
  sectionPrompts — không chặn được bằng code, ghi caveat ở B4.

## 6. ANALYZE + skill_map + leak check

### 6.1 Tool `emit_skill_map` (schema pin ngay từ design)

```jsonc
{
  "structure": {           // các field ĐẾM ĐƯỢC sẽ bị code ghi đè từ source (§4.4)
    "exam_level": "string (required)", "cefr_level": "string (required)",
    "skill": "string", "section_type": "string",
    "num_questions": "integer", "options_per_question": "integer",
    "text_genre": "string (required)",
    "word_count_range": "array[2] of integer (required)"
  },
  "per_question": [        // length = num_questions (code check)
    { "position": "integer", "skill_tested": "string (required)",
      "answer_scope": "string (required)", "distractor_pattern": "string (required)" }
  ],
  "style_notes": "string"
}
```

Prompt ANALYZE: copy §9.1 client gần nguyên văn — gồm rule chống synonym/hypernym
("describe HOW the questions test, never WHAT the text is about" — bài học v1.1).
Temperature **0.2** riêng cho analyze (client kiểm chứng; analyze là chỗ từng có
bug leak — xem decision #13).

### 6.2 Cache — migration 0023

```sql
CREATE TABLE IF NOT EXISTS public.section_skill_maps (
    section_id  uuid PRIMARY KEY REFERENCES public.sections(id) ON DELETE CASCADE,
    skill_map   jsonb NOT NULL,
    source_hash text NOT NULL,
    model       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
-- RLS bật; additive + idempotent; schema.sql lockstep.
```

- **Hash xác định:** `sha256(json.dumps(X, sort_keys=True, separators=(",",":"),
  ensure_ascii=False))` với **X = đúng payload gửi ANALYZE, sau
  `_normalize_section_positions`** — "hash cái ANALYZE thấy" loại cả lớp bug
  stale-cache/false-miss (asyncpg jsonb không giữ key order).
- Lookup theo hash; lệch → re-ANALYZE + UPSERT (lazy, không trigger).
- **Race 2 job cùng section cùng lúc:** cả hai analyze, UPSERT last-write-wins —
  chấp nhận (lãng phí 1 call, không sai dữ liệu; không cần lock).
- **Không key theo model** (decision #16): skill map từ model yếu được model mạnh
  dùng lại — chấp nhận, cột `model` chỉ để audit.

### 6.3 Leak check (pure functions)

- **Input blocklist cho shape của MÌNH:** `content` của text material + `stem` +
  `options[].text` — **LOẠI instructions/part_label** (chứa meta-words "choose/
  correct/..." lặp ở mọi section MC → false-positive vòng lặp ANALYZE_DOMAIN_LEAK).
- `build_blocklist`: proper noun (token viết hoa không đứng đầu câu) + top-15
  content noun (≥4 ký tự, ≥2 lần, trừ stopword — port nguyên list client).
- `find_leaks(skill_map_json, blocklist)`: word-boundary, case-insensitive,
  **regex-escape term** (chi tiết port dễ sót).
- **Giới hạn ghi nhận:** (a) synonym không bắt được bằng code — phòng tuyến chính
  là prompt rule (client kiểm chứng đủ); (b) heuristic là **English-ASCII** —
  "Hương"/tên có dấu không vào blocklist (Python `\w` unicode khác JS; chấp nhận,
  test KHÔNG đuổi theo JS-parity); (c) client KHÔNG có test leakCheck — test của
  ta viết fresh từ source `.ts`.

## 7. Topic pool + diversity seed

- `services/ai/topic_pool.py`: port `TOPIC_POOL` client (25 topic × 4 subject mỗi
  level A2/B1, kèm genre). **Hằng số code đợt này** — admin-config ở đợt
  editable-blocks.
- `pick_topic_and_seed(level, rng, admin_topic=None)`: admin_topic (từ
  sectionPrompts) thắng random, subject hint generic (cơ chế **yếu đã biết** — bài
  học "gà hầm" của client: generic hint vẫn lết về cùng phương pháp; mini-pool theo
  topic mới là cơ chế chính); else random topic + seed 5 chiều.
- Topic + seed (của vòng thành công) lưu vào report per-section + generation_meta.

## 8. Output contract spec mode ⭐ (N1 — KHÔNG dùng nguyên văn ví dụ client)

Client §9.2 ví dụ output theo shape của HỌ (`material_text`, options string thuần)
— dùng nguyên văn sẽ fail merge/validator của ta **tất định mọi lần**. Hợp đồng
output đợt này (MC-core):

- Vẫn forced tool-call **`emit_section` variant spec-mode**: description/schema
  KHÔNG nói "same as source" (vô nghĩa khi không có source) mà nói theo con số từ
  spec: *"exactly 1 material `{type:'text', content}`; exactly N questions, each
  `{question_type:'multiple_choice', question_data:{stem, options:[{text}] × L,
  correct_index}}` + answer_justification; also emit part_label + instructions"*.
- Block output-example trong prompt §10.2 viết lại theo shape này.
- ⚠️ **Wording deviation so với harness client** (họ freeze wording sau harness) —
  bắt buộc **re-validate bằng `ab_matrix.py`** trước khi tin (DoD #3).

## 9. Trigram guard (enforce) vs bigram shadow (giữ)

| | Trigram guard (MỚI) | Bigram verbatim_overlap (hiện có) |
|---|---|---|
| Phạm vi | chỉ spec mode | mọi version/mode |
| Vai trò | **enforce**: `>10% VÀ ≥3 trigram chung` ⇒ retry | shadow: chỉ ghi số liệu |
| Vị trí | **trước verify** (fail rẻ — 0 call AI) | sau cùng, trong try/except |
| Evidence | ngưỡng 10% từ harness 359-từ; điều kiện ≥3 bù cho material ngắn (đo: clone-template 80 từ = 26.8% vẫn bắt; text khác hẳn = 1.7%) | không ngưỡng |

## 10. Prompts (port + đánh dấu deviation)

- **§10.1 ANALYZE** = §9.1 client nguyên văn (gồm anti-synonym rule), temp 0.2.
- **§10.2 GENERATE spec** = §9.2 client, thay: (a) output block theo §8 của ta;
  (b) inject DIVERSITY_SEED/TOPIC/GENRE như client; (c) word-count vẫn ghi HARD
  LIMIT trong prompt **và** check bằng code ±15% (§4.8). Admin guidance: (B) đã
  thành topic — KHÔNG inject thêm block guidance riêng ở spec mode; (A) per-type
  prompt **không inject** vào spec mode đợt này (nghiệp vụ per-type sẽ vào
  type-core wording khi làm các core sau) — ghi rõ ở B4.
- **§10.3 VERIFY spec** = §9.4 client nguyên văn: nhận `{{STRUCTURE}}` +
  generated — structure-only **kể cả K=3** (decision #14).
- **§10.4 REWRITE mode (K≤2 / fallback)** = nguyên cấu hình v2 hiện hành.

## 11. Registry + adapter + API

- `PromptVersion` thêm field optional: `spec_mode`, `system_analyze`,
  `render_analyze`, `render_generate_spec`, `system_verify_spec`,
  `render_verify_spec`. v1/v2 để None → không đổi byte. v3 = 1 entry mới.
- `AIContentGenerator` thêm `analyze_section(payload)` (impl ở
  OpenAICompatibleGenerator — phủ 3 provider — + anthropic; tái dùng `_call_tool`,
  thêm tham số temperature **chỉ cho đường analyze mới**, không đụng path cũ).
- **API: KHÔNG endpoint/field mới.** `promptVersion: "v3"`; topic override qua
  `sectionPrompts`. Kết quả mode/topic/seed/hash/trigram% trong `report` (jsonb).
- **Provenance scope (limitation chấp nhận):** mode/topic/seed/skill_map_hash chỉ
  vào `generation_meta` ở **Mode 1** (server tự lưu). Đường Mode 2 assemble không
  mang per-part report về (và không thêm field mới đợt này) → meta của đề assemble
  chỉ có k/prompt_version/section_prompts như hiện tại. Data đầy đủ vẫn nằm trong
  job report của các lần preview/gen-part.

## 12. Bảng Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|---|---|---|---|
| 1 | Chống anchor | **Loại source khỏi prompt ở K≥3 (spec mode)** | Tiếp tục mạnh hoá prompt anti-clone (v2) | v2 chỉ chống lực hút, variance 0.11–0.79; client chứng minh giấu source ⇒ 0.0–0.6% ổn định. |
| 2 | **Kiến trúc type-core** | **Orchestration type-agnostic + core cắm theo loại câu; engine nhận biến `core`, KHÔNG tự route — việc gán core là concern bên ngoài** | Engine nguyên khối cho MC / engine tự detect type bên trong | Mỗi loại câu khác nhau đúng ở 4 chỗ (eligibility/skill-map/output-contract/structure-facts) — đóng gói thành core thì thêm loại mới không đụng orchestration; tách "chọn core" khỏi engine giữ engine pure + test được, và cho phép sau này gán manual. Chốt 2026-06-12. |
| 3 | Scope core đợt 1 | **CHỈ `core="multiple_choice"`**; shared_option/fill_blank/form_completion: thiết kế seam, implement sau (client nghiên cứu tiếp) | Nhiều core cùng lúc | Chỉ MC có harness evidence; mỗi core mới cần kiểm chứng riêng. |
| 4 | Eligibility | **6 điều kiện §3 (K≥3, KET/PET, MC thuần, đúng 1 text material, option đồng nhất, option text-only)** — pure function đầu pipeline | Eligibility lỏng hơn (mọi MC) | Mỗi điều kiện chặn một đường fail tất định đã chỉ ra ở review (đa material/0 material phá merge; trộn option phá spec 1-số; picture-MC bị thay ảnh lặng lẽ; IELTS rơi pool A2 tự mâu thuẫn). |
| 5 | Output contract | **Tool emit_section variant spec + output-block theo shape của ta (§8); merge + Tầng B giữ nguyên** | Dùng nguyên văn ví dụ output client / schema output mới | Nguyên văn client fail merge tất định (shape khác); giữ contract cũ ⇒ tái dùng toàn bộ lưới an toàn. Deviation wording phải re-validate ab_matrix. |
| 6 | Verify | **Per-mode: spec → structure-only, KHÔNG source; rewrite → v2** | Một kiểu verify mọi K | Spec làm rubric khách quan; bỏ source khỏi context vá luôn rủi ro judge chép ngược (M4 cũ). |
| 7 | Cache skill_map | **Bảng 0023 + hash = sha256(canonical JSON đúng payload ANALYZE thấy, sau normalize-position); race last-write-wins** | Không cache / cache in-memory / advisory lock | Tiết kiệm 1 call/lần gen lại; "hash cái ANALYZE thấy" diệt stale-cache; race chỉ phí 1 call, không sai dữ liệu. |
| 8 | Structure facts | **Derive bằng code từ source, ghi đè lên ANALYZE output** | Tin ANALYZE đếm | ANALYZE đếm sai 1 lần = cache độc vĩnh viễn → mọi lần gen sau fail 3/3. Client cũng derive bằng code. |
| 9 | Leak check synonym | **Lexical code + prompt rule; KHÔNG semantic check** | AI leak-judge | Client kiểm chứng prompt rule đủ; semantic code không khả thi. |
| 10 | Blocklist input | **content material + stem + option text; LOẠI instructions/part_label** | Cho cả instructions vào | "Choose the correct answer" lặp mọi section → meta-words vào top-15 → ANALYZE không bao giờ sạch. |
| 11 | Diversity | **Server random seed 5 chiều + mini-pool subject theo topic; RE-ROLL seed mỗi lần re-generate (log seed vòng thành công)** | Tin temperature / seed cố định qua retry | Client tái hiện: model không tự đa dạng; seed cố định + topic cố định ⇒ retry là replay, anchor 1 lần = cháy 3 lần. |
| 12 | Topic pool / override | **Hằng số Python; admin override qua sectionPrompts** | Bảng DB ngay / field topic riêng | Zero migration; 2 kênh trùng vai; chuyển DB ở đợt editable-blocks. |
| 13 | Temperature | **CHỈ thêm cho ANALYZE (0.2) — đường code MỚI, không đụng path generate/verify hiện hữu** | Không đụng temperature tuyệt đối / port cả gen 1.0 verify 0.2 | ANALYZE là chỗ từng có bug leak, client chạy 0.2 — chạy nóng hơn evidence là tự làm yếu 8/8; thêm chỉ ở code mới nên không còn lý do "đụng 4 adapter" như draft trước. |
| 14 | Verify K=3 depth | **Structure-only, KHÔNG đưa per_question (giữ client parity)** | Đưa per_question vào verify K=3 | REJECT có chủ đích sau review lượt 2: judge thấy per_question dễ leo thang spec-drift thành critical → loop; fixed_section chạy theo spec-fit có thể phá answer-correctness. Skill fidelity K=3 đo offline qua ab_matrix. |
| 15 | Trigram guard | **>10% VÀ ≥3 trigram chung, đặt TRƯỚC verify** | Chỉ >10% / đặt sau verify | Material ngắn (30 từ ≈ 26 trigram): 1 trigram trùng = 3.8%, formulaic opening dễ va → cần sàn tuyệt đối; đặt trước verify để mỗi lần fail tiết kiệm 2 call AI (đúng thứ tự client). |
| 16 | Cache × model | **Không key theo model (cột model chỉ audit)** | Lookup theo (hash, model) | Skill map đơn giản, tái dùng chéo model chấp nhận được; key thêm model = cache miss nhiều, tốn call. |
| 17 | Provenance assemble | **Scope về Mode 1; assemble giữ nguyên không field mới** | Thêm field partReports vào AssembleRequest | Giữ lời hứa API đóng băng; data vẫn tra được từ job report; ghi limitation rõ. |
| 18 | Merge fallback | **Spec mode: thiếu content/instructions/part_label ⇒ FAIL, không fallback từ source** | Fallback như rewrite mode | Fallback = splice text/flavour đề gốc vào đề "nội dung mới" (lạc đề + leak vào xuất bản phẩm). |
| 19 | Word count | **Check bằng code, word_count_range ±15% slack** | Prompt-only (như draft trước) | Client enforce bằng code (validator.ts) — "wc ✓" trong harness là code check; ±15% vì range do ANALYZE ước lượng, không slack = retry-burner. |

## 13. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| Blocklist false-positive trên từ generic tần-suất-cao (không định lượng được trước) | leak-feedback re-run (2 lần) + ANALYZE_DOMAIN_LEAK backstop + loại instructions khỏi input + đo thật qua ab_matrix |
| Model lách leak bằng synonym | prompt rule §9.1 client (kiểm chứng) + soi mắt định kỳ; AI leak-judge để ngỏ |
| Spec mode sinh sai đáp án (không còn source đối chiếu) | VERIFY structure-spec bắt correctness bằng evidence-quote + Tầng B + draft + admin review |
| Wording prompt lệch bản harness-frozen của client | DoD #3: re-validate toàn bộ qua ab_matrix trước khi tin |
| Mode 1: 1 section ANALYZE_DOMAIN_LEAK giết cả đề | hành vi all-or-nothing chấp nhận; khuyên Mode 2 khi thử đề gốc mới (B4 §5) |
| Worst-case ~12 call AI/section (3 analyze + 3×(1 gen + 2 verify)) | cache analyze; trigram guard đặt trước verify; job vẫn cập nhật updated_at mỗi section nên heuristic FE coi job-quá-lâu-là-stale của exam-ai-generation giữ nguyên giá trị mặc định |
| Section không đủ điều kiện "lặng lẽ" về rewrite | report.mode per-section — minh bạch với admin (B4 §2) |

## 14. Cập nhật design doc CŨ (ghi chú — sửa khi code xong, cùng đợt)

`docs/exam-ai-generation/exam-ai-generation-design.md`: §3 (K theo lượng-spec,
per-version), §6.1 (spec mode không gửi section gốc + exam_context bị cắt), §7
(verify per-mode), §8 (thêm trigram guard + word-count check), §12 (registry v3 +
bảng 0023 + temperature analyze), §15 (trỏ bảng decisions doc này).

## 15. Definition of Done (nghiệm thu khi implement)

1. **Invariant test:** text gốc (content/stem/option/title) không xuất hiện trong
   chuỗi prompt GENERATE/VERIFY spec-mode — assert tầng render, chạy cả nhánh
   retry (retry_error) lẫn nhánh có admin topic.
2. Pure functions (eligibility/blocklist/find_leaks/trigram/seed/hash/word-count)
   pass test — port test vitest client cho phần có (postprocess/topicPool/
   invariant); leakCheck test viết fresh (client không có).
3. **ab_matrix re-validation** (vì wording deviation §8): trigram overlap <10%
   trên section MC thật; 2 run cùng đề + cùng topic ra bài khác nhau — **overlap
   chéo giữa 2 bài < 5%** (client đo thực tế 0.8%); section không đủ điều kiện
   fallback đúng + report.mode đúng; K=3 skill fidelity soi tay theo per_question
   spec — **đạt ≥4/5 câu, mục tiêu 5/5** (tiêu chí client).
4. generation_meta (**Mode 1**) + job report đủ: mode, topic, seed (vòng thành
   công), skill_map_hash, trigram%, prompt_version.
5. v1/v2 hành vi không đổi (suite cũ xanh nguyên); default hiện hành (v2) không
   bị ảnh hưởng.
