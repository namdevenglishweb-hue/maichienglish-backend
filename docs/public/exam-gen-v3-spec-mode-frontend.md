---
feature: exam-gen-v3-spec-mode
type: frontend
status: approved
last-updated: 2026-06-12
author: backend
depends-on: [exam-ai-generation]
changelog: APPROVED 2026-06-12 — label K theo version, provenance Mode-1-only,
  caveat sectionPrompts, Mode1 vs Mode2 khi ANALYZE_DOMAIN_LEAK, độ trễ
  worst-case; F7: field spec chỉ có ở mode=spec
---

# Exam Generation v3 (Spec Mode) — Lưu ý cho Frontend

> **Triết lý doc này:** giải thích *ý nghĩa data & ý đồ* để FE hiểu; schema
> request/response xem **Swagger**. Render/hiển thị là quyết định của FE.
>
> **Tin quan trọng nhất cho FE: KHÔNG có endpoint/field mới.** v3 chỉ là một giá
> trị mới của `promptVersion` trên các endpoint generate sẵn có. Mọi thay đổi còn
> lại nằm trong nội dung `report` (đã là jsonb tự do).

---

## 1. Role journey

Không đổi so với exam-ai-generation hiện tại — admin-only, 2 mode (cả-đề /
từng-part), job async + poll, Save assemble. Khác biệt duy nhất trong journey:

```
Dialog gen: admin chọn K + (tuỳ chọn) model + promptVersion = "v3"
  → K≥3 + section đủ điều kiện (MC thuần, 1 đoạn text, option chữ đồng nhất,
    đề KET/PET)        ⇒ SPEC MODE: sinh nội dung mới hoàn toàn, không nhìn đề gốc
  → K≤2 / section khác ⇒ REWRITE (như v2 hiện hành, bám đề gốc)
  → poll job → review → publish (y như cũ)
```

Student/teacher/parent không thấy gì khác — đề ra vẫn là draft chờ review.

## 2. Data semantics — các field mới trong `report`

Mỗi phần tử `report.sections[]` có thêm các field dưới đây. ⚠️ **`topic` /
`diversity_seed` / `skill_map_hash` / `trigram_overlap_pct` CHỈ tồn tại khi
`mode === "spec"`** — section đi đường rewrite không có chúng (FE nhớ null-check;
`mode` thì luôn có ở job chạy v3):

- **`mode: "spec" | "rewrite"`** — section được sinh theo đường nào. Tồn tại vì
  spec mode chỉ áp cho section đạt đủ điều kiện (xem §1) ở K≥3; section listening,
  MC-shared, fill_blank, picture-MC (option bằng ảnh), section nhiều đoạn văn, đề
  IELTS... tự động rơi về rewrite *kể cả khi admin chọn K cao*. FE nên hiển thị
  cho admin để họ hiểu vì sao một part "đổi ít hơn" — hành vi đúng, không phải bug.
- **`topic` + `diversity_seed`** — spec mode không đưa đề gốc cho AI; nội dung neo
  bằng topic (server random từ pool, hoặc lấy từ sectionPrompts) + bộ seed ngẫu
  nhiên (người kể/nhân vật/bối cảnh/biến cố/chủ thể). Seed ghi lại là seed của
  **lần sinh thành công** (server có thể tự đổi seed giữa các lần retry nội bộ).
  Dùng để admin hiểu "part này sinh từ ý tưởng gì" + truy vết 2 đề trùng ý tưởng.
- **`skill_map_hash`** — provenance bản phân tích kỹ năng đã dùng; không cần render.
- **`trigram_overlap_pct`** — độ trùng câu chữ với đề gốc (%); spec mode bị chặn
  cứng (server tự retry khi vượt). Là *bằng chứng chống đạo văn* hiển thị được.
  (Field `verbatimOverlap` cũ vẫn tồn tại — thang đo khác, đừng nhầm.)

> ⚠️ **Phạm vi lưu trữ:** các field trên nằm đầy đủ trong **job report** (mọi
> mode). Nhưng `generationMeta` của exam chỉ mang chúng khi đề được tạo qua
> **Mode 1** (server tự lưu). Đề lưu qua Mode 2 Save (assemble) KHÔNG mang
> topic/seed/mode theo — limitation chấp nhận đợt này; FE muốn giữ thì đọc từ
> job report của các lần gen-part trước khi Save.

## 3. Label thang K — PHẢI điều kiện theo version đang chọn ⚠️

K mang nghĩa **khác nhau giữa các version** — label trong dialog gen phải đổi theo
giá trị dropdown version, KHÔNG thay toàn cục:

| K | v1/v2 (label hiện hành — GIỮ NGUYÊN) | v3 (label mới) |
|---|---|---|
| 1–2 | như đang có | "Bám sát đề gốc (viết lại)" |
| 3 | như đang có (viết lại mức vừa, vẫn nhìn đề gốc) | "Giữ kỹ năng từng câu, nội dung mới hoàn toàn" |
| 4 | như đang có | "Giữ khung + văn phong, nội dung tự do" |
| 5 | như đang có | "Chỉ giữ khung đề (số câu/loại/cách chấm), tự do hoàn toàn" |

Lý do: admin chạy v2 K=3 dưới label "nội dung mới hoàn toàn" sẽ nhận về bài
rewrite có nhìn đề gốc — label nói dối. Wording cuối FE quyết; trên đây là ngữ
nghĩa server đảm bảo **cho từng version**.

## 4. `sectionPrompts` ở v3 K≥3: vai trò TOPIC + 2 caveat

- Ở spec mode, `sectionPrompts[sid]` **kiêm vai trò chỉ định TOPIC**: nhập "về
  chuyến dã ngoại của lớp" → topic random bị thay cho section đó. Không nhập →
  server tự random. FE nên gợi ý ngữ nghĩa này (placeholder/hint) trong dialog.
- **Caveat 1 — hai ngữ nghĩa cùng một field:** trong cùng 1 lần gen v3, section
  đi spec mode hiểu (B) là *topic*, section fallback rewrite hiểu (B) là *guidance
  tự do* như cũ. Admin không phải lúc nào cũng đoán được section nào đi đường nào
  → FE nên hiển thị `report.mode` sau khi chạy để admin đối chiếu.
- **Caveat 2 — admin có thể tự phá chống-đạo-văn:** nếu admin paste nguyên văn
  đề gốc vào sectionPrompts thì text đó vào prompt — server không chặn được
  (ngoài scope). UI hint nên khuyên nhập *ý tưởng/chủ đề*, không phải nguyên văn.
- Riêng prompt per-type (A — section_type_prompts): **không áp dụng** cho section
  đi spec mode đợt này (chỉ còn tác dụng với section đi rewrite).

## 5. Lỗi mới + khác biệt Mode 1 vs Mode 2

- **`ANALYZE_DOMAIN_LEAK`**: bước phân tích đề gốc không tạo được bản phân tích
  "sạch" sau số lần thử. Hiếm. Hệ quả **khác nhau theo mode**:
  - **Mode 2** (gen part / preview): chỉ part đó fail — admin gen lại part / đổi
    model / hạ K cho part đó.
  - **Mode 1** (cả đề): all-or-nothing như mọi lỗi section ⇒ **cả job fail, không
    tạo đề** — hành vi đúng thiết kế. Gợi ý UX: với đề gốc lần đầu gen v3, khuyên
    admin chạy Mode 2/preview trước.
- Spec mode fail vì "quá giống đề gốc" hoặc lệch độ dài: server tự retry trong
  budget; vẫn fail thì lý do nằm trong `reason` như mọi section fail — không cần
  xử lý riêng.

## 6. Các lưu ý integrate khác

- **Backward-compat:** không gửi `promptVersion` → default hiện hành của hệ thống
  (đang là v2); gửi "v1"/"v2" → hành vi y như trước. Không field bắt buộc mới.
- **Độ trễ:** lần gen ĐẦU của một đề gốc chậm hơn rõ (thêm bước phân tích; các lần
  sau dùng cache). Worst-case một section xui (retry tối đa) có thể tới ~12 call AI
  — trên model chậm có thể vài phút cho MỘT section. Poll như cũ; đừng hạ ngưỡng
  cảnh báo stale dưới mặc định hiện tại.
- Dropdown version thêm "v3"; combo với chọn model tự do như cũ. (Engine bên trong
  nhận thêm khái niệm `core` theo loại câu — đợt này chỉ có core multiple_choice và
  server tự gán; FE **không** phải gửi gì về core.)
- Đề sinh từ spec mode vẫn mang `generatedFromExamId` trỏ đề gốc — data lineage
  không đổi, dù nội dung "không còn giống" đề gốc.
