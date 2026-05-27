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
9. [`materials` — typed content blocks](#9-materials--typed-content-blocks)
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
- Listening: audio lives inside `section.materials` (`type: "audio"` entries with `url`). Each audio has its **own** per-attempt counter, all sharing the section-wide cap value `section.maxAudioPlays`.
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
     ├─ materials[]                  (typed blocks: text / image / audio — see §9)
     ├─ maxAudioPlays                (section-wide cap value, applied per audio material)
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
| `section.type` | `multiple_choice`, `fill_blank`, `matching`, `multiple_choice_shared`, or `null` |
| `questionType` | `multiple_choice`, `fill_blank`, `matching` |

**Identity** — all IDs are UUID strings.

**Position** — 1-based ordering. `Section.position` is unique within an exam; `Question.position` is unique within a section (restarts at 1 each section). Display numbers (Q1 … Q70 on printed papers) are computed at render time — see [§10](#10-display-question-numbering).

**`section.type` — the rendering signal** — same enum as `questionType`. Set it when every question in the section is the same type and you want the FE to pick a section-wide layout:
- `'multiple_choice'` or `'fill_blank'` → vertical list, render each question independently
- `'matching'` → **shared-options table (many options)**: stems × options A–H, rendered as large table (KET Listening P5 "connect/nối" — 5 stems × 8 options)
- `'multiple_choice_shared'` → **shared-options table (few options)**: compact table, several MC questions sharing the same 2–4 options (KET Reading P2 campsites — 7 stems × 3 options A/B/C). Data shape identical to MC; only layout differs.
- `null` → mixed or no preference; FE falls back to rendering each question on its own

**`matching` vs `multiple_choice_shared`**: both render as a shared-options table, both use MC data shape (`{stem, options, correct_index}`). The distinction is semantic + layout density:
- `matching` = "nối" — typically many options (5–8), think drag-and-drop / letter-picking
- `multiple_choice_shared` = "chọn" — typically few options (2–4), think radio columns

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
      maxAudioPlays: 3,
      materials: [
        {type: 'audio', label: 'Track 1', url: 'https://...mp3'},
      ],
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

### 5.3 Endpoint summary (admin)

Successful responses: `200 OK` for GET/PUT, `201 Created` for POST that creates a resource, `204 No Content` for DELETE. Auth: every endpoint requires `Authorization: Bearer <token>`. Admin-only endpoints additionally check `role === 'admin'`. Common errors are listed in [§14](#14-error-responses-and-status-codes).

| Method | Path | Body | Response | Detailed in |
|---|---|---|---|---|
| `GET`    | `/api/exams` | — | `{items: ExamView[]}` | [§5.4 query params](#54-query-parameters) |
| `GET`    | `/api/exams/{id}` | — | `{exam: ExamView}` (optional nested `sections`) | [§5.4 `?include=`](#54-query-parameters) |
| `POST`   | `/api/exams` | `ExamCreate` (optional nested `sections[]` → `questions[]`) | `{exam: ExamView}` + optional `createdCounts` | [§5.1 nested example](#51-nested-create-fastest-path-for-a-full-exam) |
| `PUT`    | `/api/exams/{id}` | `ExamUpdate` (partial) | `{exam: ExamView}` | [§5.2 granular CRUD](#52-granular-crud--single-record-edits) |
| `POST`   | `/api/exams/{id}/publish` | — | `{exam: ExamView}` | requires ≥1 section + ≥1 active question → else `400` |
| `POST`   | `/api/exams/{id}/unpublish` | — | `{exam: ExamView}` | hides from students; data preserved |
| `DELETE` | `/api/exams/{id}` | — | `204` | soft delete; sets `deleted_at`, forces `is_published=false` |
| `DELETE` | `/api/exams/{id}/hard` | — | `204` | hard delete; CASCADEs sections/questions/attempts/answers |
| `GET`    | `/api/exams/{eid}/sections` | — | `{items: SectionView[]}` | — |
| `POST`   | `/api/exams/{eid}/sections` | `SectionCreate` (optional nested `questions[]`) | `{section: SectionView}` + optional `createdCounts` | [§5.1 nested example](#51-nested-create-fastest-path-for-a-full-exam) |
| `GET`    | `/api/sections/{sid}` | — | `{section: SectionView}` (optional nested `questions`) | [§5.4 `?include=`](#54-query-parameters) |
| `PUT`    | `/api/sections/{sid}` | `SectionUpdate` (partial) | `{section: SectionView}` | — |
| `DELETE` | `/api/sections/{sid}` | — | `204` | soft delete |
| `DELETE` | `/api/sections/{sid}/hard` | — | `204` | hard delete; CASCADEs questions/answers/state |
| `PUT`    | `/api/sections/batch` | `SectionBatchUpdateRequest` (≤100 items) | `{items: SectionView[]}` | [§5.5 batch ops](#55-batch-operations--full-bodies) |
| `POST`   | `/api/sections/batch-delete` | `{ids: string[]}` (≤100) | `204` | [§5.5 batch ops](#55-batch-operations--full-bodies), `?hard=true` opt |
| `GET`    | `/api/sections/{sid}/questions` | — | `{items: QuestionView[]}` | — |
| `POST`   | `/api/sections/{sid}/questions` | `QuestionCreate` | `{question: QuestionView}` | [§8 question types](#8-question-types-in-detail) |
| `GET`    | `/api/questions/{qid}` | — | `{question: QuestionView}` | — |
| `PUT`    | `/api/questions/{qid}` | `QuestionUpdate` (partial) | `{question: QuestionView}` | changing `question_type` requires new `question_data` |
| `DELETE` | `/api/questions/{qid}` | — | `204` | soft delete |
| `DELETE` | `/api/questions/{qid}/hard` | — | `204` | hard delete; CASCADEs answers |
| `PUT`    | `/api/questions/batch` | `QuestionBatchUpdateRequest` (≤100 items) | `{items: QuestionView[]}` | [§5.5 batch ops](#55-batch-operations--full-bodies) |
| `POST`   | `/api/questions/batch-delete` | `{ids: string[]}` (≤100) | `204` | [§5.5 batch ops](#55-batch-operations--full-bodies), `?hard=true` opt |

> **Note on field naming**: request bodies for `POST/PUT /api/exams` and `POST /api/sections/{sid}/questions` currently use **snake_case** (`question_type`, `question_data`, `duration_minutes`), while bodies for `POST/PUT /api/sections` and all response payloads use **camelCase**. This will be normalized — for now, follow the examples in this doc.

### 5.4 Query parameters

| Param | Endpoints | Allowed values | Behavior |
|---|---|---|---|
| `level` | `GET /api/exams` | `primary`, `secondary`, `KET`, `PET`, `IELTS` | Filter exams by level. Omit → all levels. |
| `skill` | `GET /api/exams` | `listening`, `reading` | Filter exams by skill. Omit → all skills. |
| `published` | `GET /api/exams` | `true`, `false` | **Admin/teacher only** — force-filter unpublished. Students/parents always see `published=true` regardless of what they send. |
| `include` | `GET /api/exams/{id}` | `sections` | When `sections`, embed `data.exam.sections[]` with nested `questions[]`. Correct-answer fields stripped for non-privileged callers. Omit → only top-level exam fields. |
| `include` | `GET /api/sections/{id}` | `questions` | When `questions`, embed `data.section.questions[]`. Same strip rule. |
| `materialIndex` | `POST /api/attempts/{aid}/sections/{sid}/audio-play` | int ≥ 0 | **Required**. 0-based index of the audio material inside `section.materials`. Each audio has its own counter; the cap value is shared via `section.maxAudioPlays`. |
| `hard` | `POST /api/sections/batch-delete`, `POST /api/questions/batch-delete` | `true`, `false` (default) | When `true`, performs hard delete (CASCADE). Default soft delete. |

Multiple values: `include` accepts comma-separated (`?include=sections,foo`) — currently only `sections` / `questions` are recognized; unknown tokens are ignored silently.

### 5.5 Batch operations — full bodies

All four batch endpoints are **all-or-nothing transactions**: if any item fails validation or its target ID doesn't exist, the whole batch rolls back with a 4xx — no partial application. Cap: **100 items per request**, exceeding returns `422`.

#### `PUT /api/sections/batch`

```jsonc
// Request body
{
  "updates": [
    {"id": "uuid-1", "instructions": "Updated rubric"},
    {"id": "uuid-2", "partLabel": "Part 2", "position": 2, "type": "matching"},
    {"id": "uuid-3", "maxAudioPlays": 2}
  ]
}
```

Each item: `id` (required, UUID) + any subset of `SectionUpdate` fields (`partLabel`, `type`, `instructions`, `materials`, `maxAudioPlays`, `position`). Items with no patch fields are rejected.

**Response 200:**
```jsonc
{
  "status": 200,
  "data": { "items": [/* updated SectionView[] in input order */] }
}
```

**Errors:**
- `404` — any `id` doesn't exist or is soft-deleted → whole batch rolls back.
- `400` — any item fails validation (e.g. invalid `materials` shape) → whole batch rolls back, `detail` names the offending index.
- `422` — body shape invalid (more than 100 items, missing `updates` key, etc.).

#### `POST /api/sections/batch-delete[?hard=true]`

```jsonc
// Request body
{"ids": ["uuid-1", "uuid-2", "uuid-3"]}
```

- Default (no query): **soft** delete (sets `deleted_at`).
- `?hard=true`: **hard** delete (DB `DELETE` row, CASCADEs through questions/answers/attempt state).

**Response:** `204 No Content`.

**Errors:**
- `404` — any `id` doesn't exist (soft delete) or is already gone (hard delete) → whole batch rolls back.

#### `PUT /api/questions/batch`

```jsonc
// Request body
{
  "updates": [
    {"id": "uuid-1", "points": 2},
    {
      "id": "uuid-2",
      "question_type": "fill_blank",
      "question_data": {"correct_answers": ["nine"], "case_sensitive": false}
    }
  ]
}
```

Each item: `id` + any subset of `QuestionUpdate` fields. **Changing `question_type` requires also sending a matching `question_data` in the same item** (else `400`).

**Response 200:** `{status: 200, data: {items: QuestionView[]}}` in input order.

#### `POST /api/questions/batch-delete[?hard=true]`

Same shape and semantics as section batch-delete.

```jsonc
{"ids": ["uuid-1", "uuid-2"]}
```

Hard delete CASCADEs through `answers`.

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

// 3. (Listening only) Play each audio material. Each play hits the server
//    to bump that audio's own counter (cap shared via section.maxAudioPlays).
for (const section of exam.sections) {
  section.materials.forEach(async (m, materialIndex) => {
    if (m.type !== 'audio') return;
    // before <audio src={m.url}>.play():
    const play = await api<AudioPlay>(
      `/api/attempts/${attemptId}/sections/${section.id}/audio-play?materialIndex=${materialIndex}`,
      {method: 'POST'},
    );
    // play = {materialIndex, audioPlayCount, maxPlays, remainingPlays}
    // 403 with "Audio play limit reached" if past cap (counter rolls back).
  });
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

A parent never takes an exam (`POST /api/attempts` returns 403 for `role=parent`). They only view their linked children's attempts. The router enforces `role === 'parent'` at the package level — students/admins calling these endpoints get `403`.

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/parents/me/children` | — | `{items: ChildView[]}` |
| `GET` | `/api/parents/me/children/{student_id}/attempts` | — | `{items: AttemptHistoryItem[]}` |
| `GET` | `/api/parents/me/children/{student_id}/attempts/{attempt_id}` | — | `AttemptDetail` (same shape as student `GET /api/attempts/{id}`) |

### 7.1 List linked children

```http
GET /api/parents/me/children
Authorization: Bearer <parent token>
```

```jsonc
// Response 200
{
  "status": 200,
  "data": {
    "items": [
      {
        "id": "uuid-of-student-1",
        "email": "student1@example.com",
        "fullName": "Nguyen Van B",
        "phone": "0909876543",
        "createdAt": "2026-05-15T10:30:00Z"
      },
      // ... more children if linked
    ]
  }
}
```

Empty list if the parent has no linked students. Use this to populate a "select child" dropdown before showing their attempts.

### 7.2 List a child's attempts

```http
GET /api/parents/me/children/{student_id}/attempts
Authorization: Bearer <parent token>
```

Returns the same shape as `GET /api/attempts/history` — newest first, capped at 100. If `student_id` is not linked to this parent → `403`.

```jsonc
// Response 200
{
  "status": 200,
  "data": {
    "items": [
      {
        "id": "uuid-of-attempt",
        "examId": "uuid-of-exam",
        "examTitle": "KET Reading Practice 01",
        "examLevel": "KET",
        "examSkill": "reading",
        "score": 8.5,
        "totalPoints": 10,
        "percentage": 85,
        "timeSpentSeconds": 1800,
        "startedAt": "2026-05-12T10:00:00Z",
        "submittedAt": "2026-05-12T10:30:00Z"
      }
    ]
  }
}
```

### 7.3 Get a specific attempt for a child

```http
GET /api/parents/me/children/{student_id}/attempts/{attempt_id}
Authorization: Bearer <parent token>
```

Same response shape as `GET /api/attempts/{id}` (see [§6 step 6](#6-student-flow--take-an-exam)) — `data.attempt`, `data.exam` (top-level metadata only), and `data.answers[]` flat list grouped by section position. Correct-answer fields are visible because attempts viewable by parents are always submitted ones.

**Errors:**
- `403 "Forbidden"` — `student_id` is not a child of this parent (or `role !== 'parent'`).
- `404 "Attempt not found"` — attempt id doesn't exist OR belongs to a student not linked to this parent (we conflate to avoid leaking existence).

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

## 9. `materials` — typed content blocks

`section.materials` is an **ordered list of typed blocks** rendered above the questions area. Three variants, discriminated by `type`:

| `type` | Fields | Use case |
|---|---|---|
| `"text"`  | `label?`, `content` | Passage / instructions / form labels. `content` may embed `{{gap:N}}` markers. |
| `"image"` | `label?`, `url`, `alt?` | Diagram, form image, illustration. FE should warn (but not block) when `alt` missing. |
| `"audio"` | `label?`, `url` | Listening clip. Per-audio counter + section-wide cap (see [§11](#11-listening-audio-replay-cap)). |

**Order is significant** — per-audio play counters are keyed by index within the materials array. KET Listening Part 2-style sections combine all three types:

```jsonc
"materials": [
  {"type": "audio", "label": "Track 1",
   "url": "https://[project].supabase.co/.../ket-l-p2.mp3"},
  {"type": "image", "label": "City Bus Tours form",
   "url": "https://[project].supabase.co/.../form.png",
   "alt": "Form with 5 fields"},
  {"type": "text", "content": "Name of guide: {{gap:1}}\nLength of tour: {{gap:2}} minutes"}
]
```

### 9.1 `{{gap:N}}` markers (text materials only)

Inside a text material's `content`, the token `{{gap:N}}` is a placeholder for the question whose `position` equals `N` **within the same section**. Render an input bound to that question at the marker location.

`{{gap:N}}` numbering is per-section across *all* text materials — markers `{{gap:1}}` in material[0] and `{{gap:5}}` in material[2] both reference questions in the same `section.questions[]` list.

**Sample render dispatcher** — iterate materials and pick a renderer per type:

```tsx
function SectionMaterials({materials, questions, onAnswer, sectionId, attemptId}: {
  materials: Material[];
  questions: Question[];
  onAnswer: (qid: string, value: string) => void;
  sectionId: string;
  attemptId: string;
}) {
  return (
    <div className="materials">
      {materials.map((m, idx) => {
        if (m.type === 'text') {
          return <PassageWithGaps key={idx} content={m.content} questions={questions} onAnswer={onAnswer} label={m.label} />;
        }
        if (m.type === 'image') {
          return <figure key={idx}>{m.label && <figcaption>{m.label}</figcaption>}<img src={m.url} alt={m.alt ?? ''} /></figure>;
        }
        if (m.type === 'audio') {
          return <AudioPlayer key={idx} url={m.url} label={m.label}
                              attemptId={attemptId} sectionId={sectionId} materialIndex={idx} />;
        }
        return null;
      })}
    </div>
  );
}
```

**`PassageWithGaps`** — parse text content, replace markers with inputs:

```tsx
function PassageWithGaps({content, questions, onAnswer, label}: {
  content: string;
  questions: Question[];
  onAnswer: (qid: string, value: string) => void;
  label?: string | null;
}) {
  const parts = content.split(/(\{\{gap:\d+\}\})/g);
  return (
    <div>
      {label && <h4>{label}</h4>}
      <p>
        {parts.map((part, i) => {
          const m = part.match(/^\{\{gap:(\d+)\}\}$/);
          if (!m) return <span key={i}>{part}</span>;
          const pos = Number(m[1]);
          const q = questions.find(qq => qq.position === pos);
          if (!q) return <span key={i} className="text-red-500">[gap {pos}?]</span>;
          return <input key={i} data-question-id={q.id}
                        onBlur={e => onAnswer(q.id, e.target.value)} />;
        })}
      </p>
    </div>
  );
}
```

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

Audio lives **inside `section.materials`** (entries with `type: "audio"`). The section declares `maxAudioPlays` — a single cap **value** that applies **independently to every audio material** in that section. The backend tracks each audio's plays separately, keyed by the audio's **0-based index** in the materials array.

Example: section has 3 audios at indices `[0, 2, 4]` (interleaved with text/image), `maxAudioPlays = 3`. The student can play each of those audios up to 3 times — they do **not** share quota.

```
POST /api/attempts/{aid}/sections/{sid}/audio-play?materialIndex=N

200 {materialIndex: N, audioPlayCount: 2, maxPlays: 3, remainingPlays: 1}
403 {detail: "Audio play limit reached (3)"}
404 {detail: "Section has no material at index N"}
400 {detail: "Material at index N is not audio"}
400 {detail: "Attempt already submitted"}
```

**Required pattern**: call the endpoint **before** starting `<audio>` playback, with the audio's `materialIndex`. If it returns 403, gray out the play button. If it succeeds, start playback. Each audio button tracks its own remaining count.

```tsx
function AudioPlayer({url, label, attemptId, sectionId, materialIndex}: {
  url: string;
  label?: string | null;
  attemptId: string;
  sectionId: string;
  materialIndex: number;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [remaining, setRemaining] = useState<number | null>(null);

  async function play() {
    try {
      const res = await api<AudioPlay>(
        `/api/attempts/${attemptId}/sections/${sectionId}/audio-play?materialIndex=${materialIndex}`,
        {method: 'POST'},
      );
      setRemaining(res.remainingPlays);
      audioRef.current?.play();
    } catch (e) {
      if (e.status === 403) setRemaining(0);  // cap reached, disable button
      else throw e;
    }
  }

  return (
    <div>
      {label && <span>{label}</span>}
      <audio ref={audioRef} src={url} />
      <button disabled={remaining === 0} onClick={play}>
        Play {remaining !== null && `(${remaining} left)`}
      </button>
    </div>
  );
}
```

**Rollback guarantee**: if the endpoint returns 403 (cap reached for *this* material), its counter does NOT advance — the increment runs in a transaction that rolls back on the exception. Safe to retry display, just don't auto-retry the play.

**Caveat — material index is positional**: if an admin reorders or inserts a material in `section.materials` while an attempt is in-flight, the index references shift and counters may mis-align. Admins should avoid editing section materials during active attempts. (v2 may switch to stable per-material UUIDs if this becomes a real problem.)

---

## 12. Mid-attempt vs post-submit stripping

The same question can return two shapes depending on whether the attempt is submitted:

| Field | Mid-attempt (before submit) | Post-submit (after submit) |
|---|---|---|
| `multiple_choice.options` / `matching.options` | always present | always present |
| `multiple_choice.correct_index` / `matching.correct_index` | **stripped** | present |
| `fill_blank.correct_answers` | **stripped** | present |
| `fill_blank.case_sensitive` | **stripped** | present |
| `multiple_choice.stem` / `matching.stem` | always present | always present |

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
| `400` | Bad request | `Attempt already submitted`, `No fields to update`, `Material at index N is not audio`, `Cannot publish exam with no active questions`, Pydantic validation errors |
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
export type SectionType = QuestionType | 'multiple_choice_shared';
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
// ===== Materials — discriminated union on `type` =====
export interface TextMaterial {
  type: 'text';
  label?: string | null;
  content: string;                 // may embed {{gap:N}}
}
export interface ImageMaterial {
  type: 'image';
  label?: string | null;
  url: string;
  alt?: string | null;
}
export interface AudioMaterial {
  type: 'audio';
  label?: string | null;
  url: string;
}
export type Material = TextMaterial | ImageMaterial | AudioMaterial;

export interface Section {
  id: string;
  position: number;
  partLabel: string | null;
  /** Rendering hint:
   *  'matching' → shared-options table (many options, connect/nối)
   *  'multiple_choice_shared' → compact shared-header table (few options)
   *  'multiple_choice' / 'fill_blank' → vertical list
   *  null → mixed / default */
  type: SectionType | null;
  instructions: string | null;
  materials: Material[];
  /** Section-wide cap value applied INDEPENDENTLY to each audio material. */
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
  materialIndex: number;            // which audio material was incremented
  audioPlayCount: number;           // post-increment count for THIS material
  maxPlays: number | null;          // = section.maxAudioPlays (shared cap value)
  remainingPlays: number | null;    // null when maxPlays is null (unlimited)
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

**Q: Materials now support `image` and `audio` — what's the difference between them and a text passage?**
A: All three are typed content blocks rendered above the questions area. Text carries `content` (may include `{{gap:N}}`); image carries `url` + optional `alt`; audio carries `url` and is gated by the per-audio replay endpoint (see [§11](#11-listening-audio-replay-cap)). The FE picks a renderer based on `material.type` — see the dispatcher in [§9](#9-materials--typed-content-blocks).

**Q: I submitted, then refreshed and got the detail screen with `correct_index` still missing. Why?**
A: Two reasons to check:
1. The attempt detail strips only while `submittedAt` is null. Refetch — if `submittedAt` is now set, correct fields appear.
2. If the user isn't the owner/admin/teacher/parent, they get 403, not 200. Check the status code.

**Q: I get 422 with `detail` as an array, not a string. Why is the shape different?**
A: 422s are FastAPI's auto-generated validation errors — they carry per-field detail. Other errors (400/401/403/404) use the `{detail: "<string>"}` shape we set in the routes. Branch on `Array.isArray(body.detail)` in your error handler.

**Q: How do I know when the access token will expire?**
A: The login response returns `token.expiresIn` in seconds. Track `Date.now() + expiresIn * 1000` and refresh ~1 minute before expiry, or refresh reactively on any 401.

**Q: Where do I get the audio file URLs?**
A: Inside `section.materials`, at every entry where `type === 'audio'`. The `url` field is ready-to-use (Supabase Storage signed URL from the `audio` bucket). Pass straight to `<audio src={...}>`. Always call `POST /api/attempts/{aid}/sections/{sid}/audio-play?materialIndex=<index>` BEFORE `.play()` to gate against the cap.

**Q: My admin form lets editors set `{{gap:N}}` markers — should I validate alignment client-side?**
A: Recommended. Parse all `{{gap:N}}` markers in materials → check each N maps to an existing `questions[].position` in the section. Catch mismatches before save.

---

For backend details (schema, internal patterns, deployment), see:
- [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md) — full spec
- [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) — repo map
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — CI/CD + Supabase + Render
