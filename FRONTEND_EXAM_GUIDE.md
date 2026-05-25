# Mai Chi English — Frontend Integration Guide (Exam Subsystem)

> **Audience**: Frontend developer integrating the exam-taking flows.
> **Scope**: exams, sections, questions, attempts (incl. listening audio).
> **Source of truth (backend)**: [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md) §3–§4.
> **API base (prod)**: `https://maichienglish-api.onrender.com`
> **API docs (Swagger)**: `https://maichienglish-api.onrender.com/docs`

---

## Document Information

| Field | Value |
|-------|-------|
| **Version** | 1.0 |
| **Date** | May 2026 |
| **Audience** | Frontend developer |
| **Status** | Active — matches backend after `refactor!(exam): introduce sections layer` |

---

## Table of Contents

1. [TL;DR — 60 seconds](#1-tldr--60-seconds)
2. [Authentication recap](#2-authentication-recap)
3. [Data model](#3-data-model)
4. [Response envelope conventions](#4-response-envelope-conventions)
5. [Admin flow — build an exam](#5-admin-flow--build-an-exam)
6. [Student flow — take an exam](#6-student-flow--take-an-exam)
7. [Parent flow — view children's attempts](#7-parent-flow--view-childrens-attempts)
8. [Question types in detail](#8-question-types-in-detail)
9. [`materials` and `{{gap:N}}` rendering](#9-materials-and-gapn-rendering)
10. [Display question numbering](#10-display-question-numbering)
11. [Listening audio replay cap](#11-listening-audio-replay-cap)
12. [Mid-attempt vs post-submit stripping](#12-mid-attempt-vs-post-submit-stripping)
13. [Permissions (RBAC) summary](#13-permissions-rbac-summary)
14. [Error responses and status codes](#14-error-responses-and-status-codes)
15. [Suggested TypeScript types](#15-suggested-typescript-types)
16. [Common gotchas / FAQ](#16-common-gotchas--faq)

---

## 1. TL;DR — 60 seconds

- Exam content is a **3-layer tree**: `Exam → Section → Question`. A Section is one "Part" of a KET/PET paper.
- All success responses wrap in `{status, data: {...}}`. Lists use `data.items`. Errors return `{detail: "<msg>"}` with the HTTP status code as the only signal.
- Auth: Bearer JWT in `Authorization: Bearer <token>`.
- **Student fetches an exam to take it**: `POST /api/attempts` returns the **full nested tree** with correct answers stripped. Don't fetch the exam separately.
- **Admin fetches the same tree** for editing: `GET /api/exams/{id}?include=sections`. Correct answers are visible.
- Question types: `multiple_choice` / `fill_blank` / `matching` — see [§8](#8-question-types-in-detail). All three share the **same** data shape and grading path; `matching` is a **rendering hint** signaled by `section.type === 'matching'`.
- Listening: each section has its **own** `audioUrl` + `maxAudioPlays`; replay is gated per section, tracked per attempt.
- Passages may contain `{{gap:N}}` markers — replace with input boxes bound to the question at `position = N` within that section.
- **Display question numbers** (Q1 … Q70) are computed at render time from section position + question index — see [§10](#10-display-question-numbering). Stored `question.position` restarts at 1 per section.

---

## 2. Authentication recap

Every endpoint except `/api/auth/login` and `/api/auth/password/*` requires a Bearer JWT.

```ts
// Login (returns user + access/refresh tokens)
const r = await fetch(`${API}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({email, password}),
});
if (!r.ok) throw new Error((await r.json()).detail);
const {data} = await r.json();
const accessToken = data.token.accessToken;   // ~60 min default
const refreshToken = data.token.refreshToken; // ~7 days default
const user = data.user; // {id, email, fullName, role, subscription:{tier, creditsRemaining}}
```

Send the token on every authenticated call:

```ts
fetch(`${API}/api/exams`, {
  headers: {'Authorization': `Bearer ${accessToken}`},
});
```

When the access token expires (401 with `detail: "Invalid or expired token"`), refresh:

```ts
const r = await fetch(`${API}/api/auth/refresh`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({refreshToken}),
});
const {data: {token}} = await r.json();
// store token.accessToken, token.refreshToken (rotates)
```

`POST /api/auth/verify` accepts a token in the header and returns `{valid, user:{id, email, role, tier}}` — handy on page load to bootstrap state without re-decoding the JWT client-side.

---

## 3. Data model

```
Exam
 ├─ id, title, level, skill, durationMinutes, description, isPublished
 └─ sections[]                       (Part 1, Part 2, ...)
     ├─ id, position, partLabel, instructions
     ├─ type                         (rendering hint — see below; nullable)
     ├─ materials[]                  (passage entries — see §9)
     ├─ audioUrl, maxAudioPlays      (listening only)
     └─ questions[]
         ├─ id, position             (per-section; referenced by {{gap:N}})
         ├─ questionType             (multiple_choice | fill_blank | matching)
         ├─ questionData             (see §8 — same shape for MC and matching)
         └─ points
```

**Allowed values**

| Field | Values |
|---|---|
| `level` | `primary`, `secondary`, `KET`, `PET`, `IELTS` |
| `skill` | `listening`, `reading` |
| `section.type` | `multiple_choice`, `fill_blank`, `matching`, or `null` |
| `questionType` | `multiple_choice`, `fill_blank`, `matching` |

**Identity** — all IDs are UUID strings.

**Position** — 1-based ordering. `Section.position` is unique within an exam; `Question.position` is unique within a section (restarts at 1 each section). Display numbers (Q1 … Q70 on printed papers) are computed at render time — see [§10](#10-display-question-numbering).

**`section.type` — the rendering signal** — same enum as `questionType`. Set it when every question in the section is the same type and you want the FE to pick a section-wide layout:
- `'multiple_choice'` or `'fill_blank'` → vertical list, render each question independently
- `'matching'` → **shared-options table**: all questions render as rows under one shared option header (KET Listening Part 5, Reading Part 2 campsites). Each row is still a separate scoring item.
- `null` → mixed or no preference; FE falls back to rendering each question on its own

`type` is a **soft hint** — the server doesn't enforce that questions within a typed section actually match. Treat it as guidance, not a guarantee.

---

## 4. Response envelope conventions

The backend never returns a bare object; everything success-shaped wraps in `{status, data}`. See [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md) §10.10.

```jsonc
// Single resource — `data.<resourceName>`
{
  "status": 200,
  "data": {
    "exam": { "id": "...", "title": "..." }
  }
}

// Action result (no named key — flat fields)
{
  "status": 200,
  "data": {
    "score": 8.5, "totalPoints": 10, "percentage": 85,
    "submittedAt": "2026-05-12T10:30:00Z"
  }
}

// List — always `data.items`
{
  "status": 200,
  "data": { "items": [ ... ] }
}

// Paginated list — `data.items` + `data.pagination`
{
  "status": 200,
  "data": {
    "items": [ ... ],
    "pagination": {"page": 1, "limit": 50, "total": 134, "totalPages": 3}
  }
}

// Error — bare detail, HTTP status carries the meaning
{ "detail": "Exam not found" }
```

**Practical helper**:

```ts
async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getAccessToken()}`,
      ...init.headers,
    },
  });
  if (r.status === 204) return undefined as T;
  const body = await r.json();
  if (!r.ok) throw new ApiError(r.status, body.detail ?? 'Request failed');
  return body.data as T;
}
```

---

## 5. Admin flow — build an exam

Two ergonomic paths. **Use nested create when you have the whole exam ready (Excel import, JSON template, fixture seed)** — one HTTP call instead of 60+. **Use granular CRUD when editing an existing exam** (drag-and-drop reorder, fix a single question, etc.).

### 5.1 Nested create — whole exam in one call

```ts
const result = await api<{exam: Exam; createdCounts: {sections: number; questions: number}}>(
  '/api/exams',
  {
    method: 'POST',
    body: JSON.stringify({
      title: 'KET Reading 01',
      level: 'KET',
      skill: 'reading',
      duration_minutes: 60,
      sections: [
        {
          partLabel: 'Part 1',
          type: 'multiple_choice',
          instructions: 'For each question, choose the correct answer.',
          materials: [],
          questions: [
            {
              question_type: 'multiple_choice',
              question_data: {
                stem: 'Chloe wants Susie...',
                options: [
                  {text: 'to clean her room.'},
                  {text: 'to stop working at home.'},
                  {text: 'to tidy up the living room.'},
                ],
                correct_index: 2,
              },
              points: 1,
            },
            // ... up to 100 questions per section
          ],
        },
        {
          partLabel: 'Part 5',
          type: 'fill_blank',
          instructions: 'Write ONE word for each gap.',
          materials: [
            {type: 'text', content: 'I {{gap:1}} home {{gap:2}} 5pm.'},
          ],
          questions: [
            {question_type: 'fill_blank', question_data: {correct_answers: ['leave']}},
            {question_type: 'fill_blank', question_data: {correct_answers: ['at']}},
          ],
        },
        // ... up to 100 sections
      ],
    }),
  },
);

// result.exam.id is the new exam UUID
// result.createdCounts = {sections: 2, questions: <total>}
// Fetch full tree with IDs:
const tree = await api<{exam: Exam}>(`/api/exams/${result.exam.id}?include=sections`);

// Publish when ready
await api(`/api/exams/${result.exam.id}/publish`, {method: 'POST'});
```

**Validation runs server-side before any INSERT**:
- Each question's `question_data` is validated per `question_type`.
- For each section, `{{gap:N}}` markers in `materials` must resolve to a question at position N (1-based, in array order). Broken markers reject the **whole batch** with 400 and tell you which marker is broken.
- Sections + questions are server-assigned `position = 1..N` in array order. Any admin-supplied `position` in nested mode is ignored — reorder the array to control ordering.
- Limits: 100 sections per exam, 100 questions per section.

You can also nest **just questions** into a single section call:

```ts
// Adding a whole new Part to an existing exam:
const result = await api<{section: Section; createdCounts: {questions: number}}>(
  `/api/exams/${examId}/sections`,
  {
    method: 'POST',
    body: JSON.stringify({
      partLabel: 'Part 2',
      type: 'matching',
      audioUrl: 'https://...mp3',
      maxAudioPlays: 3,
      questions: [/* up to 100 matching questions */],
    }),
  },
);
```

### 5.2 Granular CRUD — single-record edits

Use these when the user is editing in the admin UI (single question form, single section rename, etc.):

```ts
// Patch a single question
await api(`/api/questions/${qid}`, {
  method: 'PUT',
  body: JSON.stringify({points: 2}),
});

// Soft-delete a single section
await api(`/api/sections/${sid}`, {method: 'DELETE'});
```

### 5.3 Batch update / delete

For admin tools that mutate many rows (e.g. "renumber all sections", "delete 10 selected questions"), use the batch endpoints. Up to **100 items** per call, **all-or-nothing transaction** — one bad id rolls back the whole batch.

```ts
// Batch update questions
await api<{items: Question[]}>('/api/questions/batch', {
  method: 'PUT',
  body: JSON.stringify({
    updates: [
      {id: 'uuid-1', points: 2},
      {id: 'uuid-2', question_type: 'fill_blank',
       question_data: {correct_answers: ['nine'], case_sensitive: false}},
    ],
  }),
});

// Batch soft-delete sections
await api('/api/sections/batch-delete', {
  method: 'POST',
  body: JSON.stringify({ids: ['uuid-1', 'uuid-2']}),
});

// Batch hard-delete (CASCADEs)
await api('/api/sections/batch-delete?hard=true', {
  method: 'POST',
  body: JSON.stringify({ids: ['uuid-1', 'uuid-2']}),
});
```

Same shape for `/api/questions/batch-delete`.

**Endpoint summary (admin)**

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/api/exams?published=&level=&skill=` | List exams (admin can include unpublished) |
| `GET`    | `/api/exams/{id}?include=sections` | Get exam, optionally with full section tree |
| `POST`   | `/api/exams` | Create exam (optionally with nested sections+questions) |
| `PUT`    | `/api/exams/{id}` | Patch exam fields |
| `POST`   | `/api/exams/{id}/publish` | Publish (gated) |
| `POST`   | `/api/exams/{id}/unpublish` | Unpublish |
| `DELETE` | `/api/exams/{id}` | Soft-delete |
| `DELETE` | `/api/exams/{id}/hard` | Hard-delete (CASCADE) |
| `GET`    | `/api/exams/{eid}/sections` | List sections of an exam |
| `POST`   | `/api/exams/{eid}/sections` | Create section (optionally with nested questions) |
| `GET`    | `/api/sections/{sid}?include=questions` | Get section, optionally with questions |
| `PUT`    | `/api/sections/{sid}` | Patch section |
| `DELETE` | `/api/sections/{sid}` | Soft-delete section |
| `DELETE` | `/api/sections/{sid}/hard` | Hard-delete section (CASCADE) |
| `PUT`    | `/api/sections/batch` | Batch update up to 100 sections (one txn) |
| `POST`   | `/api/sections/batch-delete[?hard=true]` | Batch delete up to 100 sections |
| `GET`    | `/api/sections/{sid}/questions` | List questions of a section |
| `POST`   | `/api/sections/{sid}/questions` | Create question |
| `GET`    | `/api/questions/{qid}` | Get a single question |
| `PUT`    | `/api/questions/{qid}` | Patch question |
| `DELETE` | `/api/questions/{qid}` | Soft-delete question |
| `DELETE` | `/api/questions/{qid}/hard` | Hard-delete question (CASCADE) |
| `PUT`    | `/api/questions/batch` | Batch update up to 100 questions (one txn) |
| `POST`   | `/api/questions/batch-delete[?hard=true]` | Batch delete up to 100 questions |

> **Note on field naming**: request bodies for `POST/PUT /api/exams` and `POST /api/sections/{sid}/questions` currently use **snake_case** (`question_type`, `question_data`, `duration_minutes`), while bodies for `POST/PUT /api/sections` and all response payloads use **camelCase**. This will be normalized — for now, follow the examples in this doc.

---

## 6. Student flow — take an exam

The whole exam tree comes back from a single call.

```ts
// 1. Browse published exams
const {items: exams} = await api<{items: Exam[]}>('/api/exams?level=KET&skill=reading');

// 2. Start an attempt — returns the nested tree with correct answers stripped
const start = await api<AttemptStart>('/api/attempts', {
  method: 'POST',
  body: JSON.stringify({examId: chosenExamId}),
});
const {attemptId, exam, startedAt} = start;
// exam.sections[*].questions[*].questionData has NO correct_index/correct_answers/correct_pairs

// 3. (Listening only) Play the audio. Each play hits the server to bump the counter.
for (const section of exam.sections) {
  if (!section.audioUrl) continue;
  // before <audio src={section.audioUrl}>.play():
  const play = await api<AudioPlay>(
    `/api/attempts/${attemptId}/sections/${section.id}/audio-play`,
    {method: 'POST'},
  );
  // play = {audioPlayCount, maxAudioPlays, remainingPlays}
  // 403 with "Audio play limit reached" if past cap.
}

// 4. Collect answers in memory as the student progresses.
const answers: Array<{questionId: string; studentAnswer: unknown}> = [];

// 5. Submit when done
const result = await api<SubmitResult>(`/api/attempts/${attemptId}/submit`, {
  method: 'POST',
  body: JSON.stringify({answers, timeSpentSeconds: 1800}),
});
// result = {score, totalPoints, percentage, submittedAt}

// 6. Show the detail screen — now correct answers ARE visible
const detail = await api<AttemptDetail>(`/api/attempts/${attemptId}`);
// detail.answers is a flat list ordered by section.position then question.position;
// each item has sectionId/sectionPosition/sectionPartLabel so you can group client-side.
```

**Endpoint summary (student)**

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/exams` | List published exams |
| `GET`  | `/api/exams/{id}` | Get one published exam (top-level only) |
| `POST` | `/api/attempts` | Start attempt — returns nested tree, stripped |
| `POST` | `/api/attempts/{aid}/sections/{sid}/audio-play` | Bump per-section audio counter |
| `POST` | `/api/attempts/{aid}/submit` | Submit answers + receive grade |
| `GET`  | `/api/attempts/{aid}` | Get attempt detail (per-question, grouped by section) |
| `GET`  | `/api/attempts/history` | Own attempt history (newest first, capped at 100) |

> **Tier limits** (enforced server-side at `/api/attempts`): Free = 5/month, Basic = 50/month, Pro/Ultra = unlimited. Past the limit → `403 "Monthly attempt limit reached"`. Show the cap in your UI before the user tries.

---

## 7. Parent flow — view children's attempts

A parent never takes an exam (`POST /api/attempts` returns 403 for `role=parent`). They only view their linked children's attempts.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/parents/me/children` | List students linked to the parent |
| `GET` | `/api/parents/me/children/{sid}/attempts` | Child's attempt history |
| `GET` | `/api/parents/me/children/{sid}/attempts/{aid}` | Single attempt detail (same shape as student detail) |

---

## 8. Question types in detail

### 8.1 `multiple_choice`

**Server `questionData` (admin sees full)**
```jsonc
{
  "stem": "Chloe wants Susie...",       // optional
  "options": [
    {"text": "to clean her room."},
    {"text": "to stop working at home."},
    {"text": "to tidy up the living room."}
  ],
  "correct_index": 2                     // stripped mid-attempt
}
```

**With image options (Listening Part 1)**
```jsonc
{
  "stem": "How did the woman travel to work this morning?",
  "options": [
    {"image_url": "https://.../car.png"},
    {"image_url": "https://.../train.png"},
    {"image_url": "https://.../bus.png"}
  ],
  "correct_index": 2
}
```

Each option has `text` and/or `image_url`. Either may be missing; at least one is always present.

**Rendering hint** — radio buttons:
- If every option has `text` only → standard list.
- If every option has `image_url` only → grid of images (Listening P1).
- Mixed: render whichever the option has.

**`studentAnswer` to send back** — the integer index (0-based) of the chosen option:
```jsonc
{"questionId": "uuid", "studentAnswer": 2}
```

### 8.2 `fill_blank`

**Server `questionData`**
```jsonc
{
  "correct_answers": ["nine", "9"],     // any one matches; stripped mid-attempt
  "case_sensitive": false                // stripped mid-attempt
}
```

**Rendering hint** — text input. The prompt usually lives in the surrounding passage (see [§9](#9-materials-and-gapn-rendering)).

**`studentAnswer`** — a single string:
```jsonc
{"questionId": "uuid", "studentAnswer": "nine"}
```

> Grading trims whitespace and (when `case_sensitive: false`) lowercases both sides before comparison. Trailing spaces / capitalization don't fail correct answers.

### 8.3 `matching` (also called "connect" / nối)

**Same data shape as `multiple_choice`** — `{stem, options, correct_index}`. Each matching question is **one independently-scored row** of a shared-options table (e.g. KET Listening Part 5, Q21 "Anthea" → A–H presents).

The `matching` label is purely a **rendering signal** — it tells the FE to render this question as a row in a shared-options table when grouped with siblings. The server doesn't gate it differently from MC at the data or grading layer.

**Server `questionData`** (Listening P5 Q21):
```jsonc
{
  "stem": "Anthea",
  "options": [
    {"text": "art equipment"}, {"text": "bag"}, {"text": "book"},
    {"text": "chocolate"}, {"text": "concert ticket"}, {"text": "jewellery"},
    {"text": "perfume"}, {"text": "picture"}
  ],
  "correct_index": 5
}
```

Each of Q21–Q25 carries its own copy of the 8 options (denormalized — single source on section level was considered and rejected as over-engineered).

**Rendering** — check `section.type === 'matching'`:
- If yes: render the section as a **single table** with one row per question. The option header is taken from `questions[0].options` (all questions in the section share the same set). Each row has the question's `stem` on the left and radio buttons under the option columns.
- If no (mismatched section.type — soft hint failure): fall back to rendering each matching question as a standalone MC.

**`studentAnswer`** — integer index, same as MC:
```jsonc
{"questionId": "uuid", "studentAnswer": 5}
```

**Grading** — `student_answer === correct_index`. Same path as MC. Per-question scoring (1 mark each by KET convention) — no "all-or-nothing" issue because each row is its own question.

**Sample table-layout render (React-ish pseudocode)**:
```tsx
function MatchingSection({section}: {section: Section}) {
  const qs = section.questions;
  if (qs.length === 0) return null;
  const options = qs[0].questionData.options;  // shared across siblings

  return (
    <table>
      <thead>
        <tr>
          <th></th>
          {options.map((o, i) => <th key={i}>{String.fromCharCode(65 + i)}<br/>{o.text}</th>)}
        </tr>
      </thead>
      <tbody>
        {qs.map((q, qi) => (
          <tr key={q.id}>
            <td>{displayNumber(qi)}  {q.questionData.stem}</td>
            {options.map((_, oi) => (
              <td key={oi}>
                <input type="radio" name={q.id} value={oi}
                       onChange={() => recordAnswer(q.id, oi)} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

### 8.4 Skipped questions

`studentAnswer: null` (or simply omitting the entry from the `answers[]` array) marks the question as skipped — counted wrong, 0 points.

---

## 9. `materials` and `{{gap:N}}` rendering

A section may include one or more **passages** in `section.materials`. Each entry has shape:

```jsonc
{
  "type": "text",
  "label": "From: Bea  |  To: Tania",   // optional heading
  "content": "Are you busy {{gap:1}} the moment? {{gap:2}} you remember..."
}
```

Inside `content`, the token `{{gap:N}}` is a placeholder for the question whose `position` equals `N` **within the same section**. Render an input bound to that question at the marker location.

**Sample parser (React)**

```tsx
function PassageWithGaps({content, questions, onAnswer}: {
  content: string;
  questions: Question[];                          // questions of this section
  onAnswer: (qid: string, value: string) => void;
}) {
  const parts = content.split(/(\{\{gap:\d+\}\})/g);
  return (
    <p>
      {parts.map((part, i) => {
        const m = part.match(/^\{\{gap:(\d+)\}\}$/);
        if (!m) return <span key={i}>{part}</span>;
        const pos = Number(m[1]);
        const q = questions.find(qq => qq.position === pos);
        if (!q) return <span key={i} className="text-red-500">[gap {pos}?]</span>;
        return (
          <input key={i}
            data-question-id={q.id}
            onBlur={e => onAnswer(q.id, e.target.value)} />
        );
      })}
    </p>
  );
}
```

**Multi-passage example** (Reading P5 dialogue):

```jsonc
"materials": [
  {"type": "text", "label": "From: Bea  |  To: Tania",
   "content": "Are you busy {{gap:1}} the moment? {{gap:2}} you remember..."},
  {"type": "text", "label": "From: Tania  |  To: Bea",
   "content": "That sounds great! {{gap:5}} would you like to go?..."}
]
```

Note that `{{gap:N}}` numbering is per-section across *all* materials — markers 1, 2 in passage A and 5 in passage B all reference distinct questions in the same `section.questions[]` list.

> Today only `type: "text"` is supported in materials. Audio is on `section.audioUrl`, not inside materials.

---

## 10. Display question numbering

Stored `question.position` **restarts at 1** in each section. Printed exam papers show **absolute** numbers running across the whole paper (Q1, Q2, …, Q60, Q70). Compute these at render time — never store them in the DB.

**Algorithm** — cumulative offset across preceding sections + 1-based index within the current section:

```ts
/** Map each (sectionIdx, questionIdx) to a display number. */
function buildNumberMap(sections: Section[]): Map<string, number> {
  const map = new Map<string, number>();
  let offset = 0;
  for (const s of sections) {
    s.questions.forEach((q, qi) => {
      map.set(q.id, offset + qi + 1);   // qi is 0-based array index
    });
    offset += s.questions.length;
  }
  return map;
}

// Usage
const numbers = buildNumberMap(exam.sections);
numbers.get(someQuestion.id);   // e.g. 23
```

**Important**: use the **array index** (`qi`), not `question.position`. The two differ when a question is soft-deleted — `list_questions_by_section` filters out deleted rows, so the array is dense, but stored positions may have gaps (e.g. 1, 2, **4**, 5 after position-3 was deleted). Display should renumber to 1, 2, 3, 4 — the array index gives that for free.

**Why not store absolute numbers?**
- Admin reorders sections (swap Part 1 ↔ Part 2) → all absolute numbers shift. With derived numbering, just re-derive on next render — no schema change, no migration.
- Admin inserts a section in the middle → same.
- Admin soft-deletes a question → renumbering is automatic.

**Stable references in graded results** — the underlying `question_id` (UUID) is what attempts reference. Display numbers are cosmetic. If you need a frozen "Q42" in an emailed result, snapshot it at submit time client-side; the server doesn't store display numbers.

**KET "Example 0" rows** — KET papers often show an example with display number `0` (e.g. Listening P5: "0 Cara | A"). The example **is not a scoring item** and should NOT be a `Question` row. Store it as part of `section.instructions` text or as a `material` entry — that way display numbering naturally starts at 1 (or 21 with the right offset from previous sections) without skipping anything.

---

## 11. Listening audio replay cap

Each **listening section** can declare `audioUrl` and `maxAudioPlays` (e.g. 3). The backend tracks plays **per attempt × per section** in `attempt_section_state`. Past the cap, plays are rejected.

```
POST /api/attempts/{aid}/sections/{sid}/audio-play

200 {audioPlayCount: 2, maxAudioPlays: 3, remainingPlays: 1}
403 {detail: "Audio play limit reached (3)"}
400 {detail: "Section has no audio"}
400 {detail: "Attempt already submitted"}
```

**Required pattern**: call the endpoint **before** starting `<audio>` playback. If it returns 403, gray out the play button. If it succeeds, start playback.

```tsx
async function play(attemptId: string, sectionId: string, audioEl: HTMLAudioElement) {
  try {
    const res = await api<AudioPlay>(
      `/api/attempts/${attemptId}/sections/${sectionId}/audio-play`,
      {method: 'POST'},
    );
    audioEl.play();
    setRemainingPlays(res.remainingPlays);
  } catch (e) {
    if (e.status === 403) setRemainingPlays(0);  // cap reached, disable button
    else throw e;
  }
}
```

**Rollback guarantee**: if the endpoint returns 403 (cap reached), the counter does NOT advance — the increment runs in a transaction that rolls back on the exception. Safe to call repeatedly without polluting state.

---

## 12. Mid-attempt vs post-submit stripping

The same question can return two shapes depending on whether the attempt is submitted:

| Field | Mid-attempt (before submit) | Post-submit (after submit) |
|---|---|---|
| `multiple_choice.options` | always present | always present |
| `multiple_choice.correct_index` | **stripped** | present |
| `fill_blank.correct_answers` | **stripped** | present |
| `fill_blank.case_sensitive` | **stripped** | present |
| `matching.correct_pairs` | **stripped** | present |
| `matching.left` / `matching.right` | always present | always present |

Endpoints that strip mid-attempt (for non-privileged callers):
- `POST /api/attempts` (the initial fetch returning the tree)
- `GET /api/attempts/{aid}` while `submittedAt` is null
- `GET /api/exams/{id}?include=sections` for students
- `GET /api/sections/{id}?include=questions` for students

Admin / teacher always see full data.

---

## 13. Permissions (RBAC) summary

| Role | What they can do |
|---|---|
| `student` | List/read published exams; start own attempts; submit; view own results + history |
| `parent` | List linked children; view children's attempts (no taking) |
| `teacher` | All student permissions + view any attempt |
| `admin` | Everything: full exam/section/question CRUD, publish, manage users, manage subscriptions |

Common 403s:
- `Admin access required` — admin-only endpoint
- `Parents cannot start exam attempts` — `POST /api/attempts` with `role=parent`
- `Not the owner of this attempt` — submit / audio-play on someone else's attempt
- `Not allowed to view this attempt` — `GET /api/attempts/{id}` with no valid claim (not owner, not staff, not parent of owner)
- `Monthly attempt limit reached (N)` — tier cap hit

---

## 14. Error responses and status codes

Errors come back as `{"detail": "<msg>"}`. The HTTP status is what you branch on.

| Status | Meaning | Common causes |
|---|---|---|
| `400` | Bad request | `Attempt already submitted`, `No fields to update`, `Section has no audio`, `Cannot publish exam with no active questions`, Pydantic validation errors |
| `401` | Unauthorized | Missing/expired/invalid bearer token. Refresh via `/api/auth/refresh` |
| `403` | Forbidden | RBAC denial, tier limit, audio cap, non-owner |
| `404` | Not found | Exam/section/question/attempt doesn't exist *or* exists-but-unpublished and caller isn't privileged (we deliberately conflate to avoid leaking existence) |
| `422` | Unprocessable Entity | Pydantic body validation (`detail` is an array of error objects in this case, not a string) |
| `503` | Service unavailable | DB unreachable on `/db-ping` |

**422 has a different `detail` shape** (auto-generated by FastAPI):
```jsonc
{
  "detail": [
    {"loc": ["body", "email"], "msg": "value is not a valid email", "type": "value_error.email"}
  ]
}
```

Display the first item's `msg` or surface inline to the offending field.

---

## 15. Suggested TypeScript types

Drop-in for a TS project. These mirror the Pydantic schemas exactly.

```ts
// ===== Enums =====
export type Level = 'primary' | 'secondary' | 'KET' | 'PET' | 'IELTS';
export type Skill = 'listening' | 'reading';
export type QuestionType = 'multiple_choice' | 'fill_blank' | 'matching';
export type Role = 'student' | 'teacher' | 'admin' | 'parent';
export type Tier = 'free' | 'basic' | 'pro' | 'ultra';

// ===== Question data shapes (per-type) =====
export interface MCOption { text?: string; image_url?: string; }

/** Shared shape for both `multiple_choice` and `matching` questions —
 *  matching is just MC rendered as a shared-options table (signaled by
 *  section.type === 'matching'). */
export interface ChoiceQuestionData {
  stem?: string;
  options: MCOption[];
  /** Present only when caller is privileged or attempt is submitted. */
  correct_index?: number;
}
/** Alias for clarity at use sites. */
export type MultipleChoiceData = ChoiceQuestionData;
export type MatchingData = ChoiceQuestionData;

export interface FillBlankData {
  /** Present only when caller is privileged or attempt is submitted. */
  correct_answers?: string[];
  case_sensitive?: boolean;
}
export type QuestionData = ChoiceQuestionData | FillBlankData;

// ===== Tree =====
export interface Question {
  id: string;
  position: number;
  questionType: QuestionType;
  questionData: QuestionData;
  points: number;
}
export interface Material {
  type: 'text';
  label?: string | null;
  content: string;
}
export interface Section {
  id: string;
  position: number;
  partLabel: string | null;
  /** Rendering hint — when 'matching', render this section as a single
   *  shared-options table; otherwise render each question independently. */
  type: QuestionType | null;
  instructions: string | null;
  materials: Material[];
  audioUrl: string | null;
  maxAudioPlays: number | null;
  questions: Question[];          // present when fetched via attempt-start or ?include=
}
export interface Exam {
  id: string;
  title: string;
  level: Level;
  skill: Skill;
  durationMinutes: number;
  description: string | null;
  isPublished: boolean;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
  sections?: Section[];           // present when fetched via ?include=sections
}

// ===== Attempts =====
export interface AttemptStart {
  attemptId: string;
  exam: Exam & {sections: Section[]};
  startedAt: string;
}
export interface AnswerInput {
  questionId: string;
  /** number (option index) for MC and matching, string for fill_blank, null = skip. */
  studentAnswer: number | string | null;
}
export interface SubmitResult {
  score: number;
  totalPoints: number;
  percentage: number;
  submittedAt: string;
}
export interface AudioPlay {
  audioPlayCount: number;
  maxAudioPlays: number | null;
  remainingPlays: number | null;
}
export interface AnswerDetail {
  answerId: string;
  questionId: string;
  sectionId: string;
  sectionPosition: number;
  sectionPartLabel: string | null;
  position: number;
  questionType: QuestionType;
  questionData: QuestionData;     // includes correct_* post-submit
  points: number;
  studentAnswer: AnswerInput['studentAnswer'];
  isCorrect: boolean | null;
  pointsEarned: number;
}
export interface AttemptDetail {
  attempt: {
    id: string;
    userId: string;
    examId: string;
    score: number | null;
    totalPoints: number | null;
    percentage: number | null;
    timeSpentSeconds: number | null;
    startedAt: string;
    submittedAt: string | null;
  };
  exam: {id: string; title: string; level: Level; skill: Skill};
  answers: AnswerDetail[];        // ordered by section.position then question.position
}

// ===== Envelope =====
export interface ApiOk<T> { status: number; data: T; }
export interface ApiErr { detail: string | Array<{loc: string[]; msg: string; type: string}>; }

// ===== Convenience =====
export class ApiError extends Error {
  constructor(public status: number, public detail: ApiErr['detail']) {
    super(typeof detail === 'string' ? detail : detail[0]?.msg ?? 'Request failed');
  }
}
```

---

## 16. Common gotchas / FAQ

**Q: Why does `GET /api/exams/{id}` not include sections by default?**
A: For listing/browsing you only want metadata. Use `?include=sections` for the full tree. Student exam-taking should use `POST /api/attempts` instead — same tree but with correct answers stripped and an attempt row created in one round-trip.

**Q: Question positions in two different sections both start at 1 — is that a bug?**
A: No. `position` is unique per `(section_id, position)`. The numbering shown in printed exam papers ("21–25") is **display numbering** — derive client-side per [§10](#10-display-question-numbering). Storing absolute numbers would break the moment an admin reorders sections or soft-deletes a question; deriving means you never have to migrate.

**Q: A `matching` question's `questionData` looks identical to a `multiple_choice` question's. What's the difference?**
A: At the data and grading layer, nothing — they share `{stem, options, correct_index}` and the same grading path. The only distinction is the `questionType` label, which combined with `section.type === 'matching'` tells the FE to render the section as a shared-options table instead of a vertical list. Each matching question is independently scored (1 mark per row by KET convention).

**Q: Should I store the "Q42" number on attempt results so it stays stable after admin edits?**
A: Only if you really need it. The underlying `question_id` (UUID) is stable; display numbers are derived from current section/question ordering. If an admin reorders content, the same UUID may map to a different printed number. Take a client-side snapshot of `buildNumberMap(exam.sections)` at submit time if you want frozen labels in emailed results.

**Q: KET papers have an "Example: 0 Cara — A" row. Where does it live in the data model?**
A: It's not a `Question` row. Put it in `section.instructions` (e.g. *"What present will each person give? Example: 0 Cara — A"*) or as a `material` entry. That way display numbering for the real questions naturally starts at the right offset without a "Q0" gap.

**Q: The `{{gap:N}}` in a material content references a question that doesn't exist. Now what?**
A: The backend doesn't enforce gap/question alignment. Render a visible placeholder (`[gap N?]`) so the admin notices. Don't crash.

**Q: I called `/audio-play` 3 times and the 4th returned 403. Did the counter advance to 4?**
A: No. The over-cap call rolls back the increment in its DB transaction. The counter stays at 3. Safe to retry display, just don't auto-retry the play.

**Q: A material has `audio` or `image` `type` — what do I do?**
A: Today only `type: "text"` exists. If you see anything else, fail soft (ignore the entry, log a warning) — the schema may grow.

**Q: I submitted, then refreshed and got the detail screen with `correct_index` still missing. Why?**
A: Two reasons to check:
1. The attempt detail strips only while `submittedAt` is null. Refetch — if `submittedAt` is now set, correct fields appear.
2. If the user isn't the owner/admin/teacher/parent, they get 403, not 200. Check the status code.

**Q: I get 422 with `detail` as an array, not a string. Why is the shape different?**
A: 422s are FastAPI's auto-generated validation errors — they carry per-field detail. Other errors (400/401/403/404) use the `{detail: "<string>"}` shape we set in the routes. Branch on `Array.isArray(body.detail)` in your error handler.

**Q: How do I know when the access token will expire?**
A: The login response returns `token.expiresIn` in seconds. Track `Date.now() + expiresIn * 1000` and refresh ~1 minute before expiry, or refresh reactively on any 401.

**Q: Where do I get the audio file URLs?**
A: Backend returns ready-to-use URLs in `section.audioUrl`. They point at Supabase Storage in the `audio` bucket. Pass straight to `<audio src={...}>`.

**Q: My admin form lets editors set `{{gap:N}}` markers — should I validate alignment client-side?**
A: Recommended. Parse all `{{gap:N}}` markers in materials → check each N maps to an existing `questions[].position` in the section. Catch mismatches before save.

---

For backend details (schema, internal patterns, deployment), see:
- [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md) — full spec
- [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) — repo map
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — CI/CD + Supabase + Render
