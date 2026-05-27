# Mai Chi English — Attempt Lifecycle Specification

> **Scope**: everything about how a student starts, saves, resumes, submits,
> or abandons an exam attempt. Covers the "1 active attempt globally" rule,
> manual save, resume flow, abandon, and per-audio replay tracking.
>
> **Source of truth** for the attempt subsystem; complements
> [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md) §3.7–§3.8 / §4.7.

---

## Document Information

| Field | Value |
|-------|-------|
| **Version** | 1.0 |
| **Date** | May 2026 |
| **Status** | Design — not yet implemented |
| **Audience** | Backend developer, Frontend developer |

---

## Table of Contents

1. [Design principles](#1-design-principles)
2. [Database schema changes](#2-database-schema-changes)
3. [Attempt state machine](#3-attempt-state-machine)
4. [API specification](#4-api-specification)
   - 4.1 Start attempt (idempotent)
   - 4.2 Get active attempt
   - 4.3 Save answers (manual save)
   - 4.4 Submit attempt
   - 4.5 Abandon attempt
   - 4.6 Audio play (per-material counter)
   - 4.7 Get attempt detail
   - 4.8 Get attempt history
5. [Business rules](#5-business-rules)
6. [Frontend integration guide](#6-frontend-integration-guide)
7. [Edge cases & decisions](#7-edge-cases--decisions)

---

## 1. Design principles

| Principle | Rule |
|-----------|------|
| **1 active attempt globally** | A student may have at most **one** non-submitted, non-abandoned attempt at any time, across all exams. Enforced by a partial unique index on `(user_id) WHERE submitted_at IS NULL AND NOT is_abandoned`. |
| **Idempotent start** | Calling `POST /api/attempts` for an exam the student already has in-progress returns the **existing** attempt (200), not a new one (201). Quota is only consumed on genuine new creation. |
| **Explicit save** | Answers are persisted to the server only when the student clicks **"Save"** or when the FE triggers save on critical events. No continuous auto-save — the student controls when to persist. |
| **Resume anywhere** | After a save, the student can close the browser, switch devices, and resume later. `GET /api/attempts/{id}` returns saved answers for prefill. |
| **Abandon is irreversible** | Once abandoned, the attempt is finalized with `score = 0`. It counts toward the monthly quota. The student can then start a fresh attempt for the same exam. |
| **Submit is final** | Once submitted, answers are graded and the attempt is locked. No re-submit, no edit. |

---

## 2. Database schema changes

### 2.1 Migration 0008 — `is_abandoned` flag + one-active-per-user index

```sql
-- Add abandonment flag
ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS is_abandoned boolean NOT NULL DEFAULT false;

-- Enforce: at most 1 active (non-submitted, non-abandoned) attempt per user.
-- Race-condition safe — DB rejects the second INSERT even under concurrency.
CREATE UNIQUE INDEX IF NOT EXISTS attempts_one_active_per_user
  ON public.attempts (user_id)
  WHERE submitted_at IS NULL AND NOT is_abandoned;
```

### 2.2 Updated `attempts` table (post-migration)

```sql
CREATE TABLE public.attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id              uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score                numeric(6,2),
  total_points         numeric(6,2),
  percentage           numeric(5,2),
  time_spent_seconds   int,
  is_abandoned         boolean NOT NULL DEFAULT false,        -- NEW
  started_at           timestamptz NOT NULL DEFAULT now(),
  submitted_at         timestamptz
);

-- Only 1 active attempt per user at any time
CREATE UNIQUE INDEX attempts_one_active_per_user
  ON public.attempts (user_id)
  WHERE submitted_at IS NULL AND NOT is_abandoned;
```

### 2.3 `answers` table — no schema change

The existing `answers` table already supports the save flow. Mid-attempt
saves UPSERT rows with `is_correct = NULL` and `points_earned = 0`.
Submit overwrites with graded values.

```sql
-- Existing (unchanged):
CREATE TABLE public.answers (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id      uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  question_id     uuid NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  student_answer  jsonb,
  is_correct      boolean,          -- NULL while saved mid-attempt; set on submit
  points_earned   int NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);
```

### 2.4 `attempt_section_state` — no schema change

Per-section audio counters (`audio_play_counts` jsonb) and resume
timestamps are already in place from migration 0006. No changes needed.

---

## 3. Attempt state machine

```
                          ┌─────────────┐
                          │   (none)    │  no active attempt
                          └──────┬──────┘
                                 │
                    POST /attempts {examId}
                    (201 Created — quota +1)
                                 │
                          ┌──────▼──────┐
                   ┌─────►│ in_progress │◄─────┐
                   │      └──┬───┬───┬──┘      │
                   │         │   │   │         │
              PATCH /answers │   │   │  POST /attempts {same examId}
              (save progress)│   │   │  (200 OK — resume, no quota)
                   │         │   │   │         │
                   └─────────┘   │   └─────────┘
                                 │
                    ┌────────────┼────────────┐
                    │                         │
           POST /submit              POST /abandon
           (grade + finalize)        (score=0, finalize)
                    │                         │
             ┌──────▼──────┐          ┌───────▼───────┐
             │  submitted  │          │   abandoned   │
             │  (graded)   │          │  (score = 0)  │
             └─────────────┘          └───────────────┘
                    │                         │
                    └────────┬────────────────┘
                             │
                      (user can now start
                       a new attempt)
```

**State definitions:**

| State | `submitted_at` | `is_abandoned` | Can resume? | Can save? | Can submit? |
|-------|:-:|:-:|:-:|:-:|:-:|
| `in_progress` | NULL | false | yes | yes | yes |
| `submitted` | set | false | no | no | no |
| `abandoned` | set | true | no | no | no |

---

## 4. API specification

All endpoints require `Authorization: Bearer <token>`. Response
envelope: `{status, data}` for success; `{detail}` for errors (see
[`FRONTEND_EXAM_GUIDE.md`](FRONTEND_EXAM_GUIDE.md) §4 / §14).

### 4.1 Start attempt (idempotent)

```
POST /api/attempts
```

**Request:**
```json
{
  "examId": "uuid-of-published-exam"
}
```

**Three outcomes:**

#### Case A — no active attempt exists → **create new** (201)

The student has no in-progress attempt for any exam. A new attempt is
created. Monthly quota is decremented.

**Response 201:**
```jsonc
{
  "status": 201,
  "data": {
    "attemptId": "uuid",
    "isResume": false,                     // NEW flag
    "exam": {
      "id": "uuid",
      "title": "KET Reading 01",
      "level": "KET",
      "skill": "reading",
      "durationMinutes": 60,
      "description": "...",
      "sections": [
        {
          "id": "uuid",
          "position": 1,
          "partLabel": "Part 1",
          "type": "multiple_choice",
          "instructions": "...",
          "materials": [...],
          "maxAudioPlays": null,
          "questions": [
            {
              "id": "uuid",
              "position": 1,
              "questionType": "multiple_choice",
              "questionData": { "stem": "...", "options": [...] },
              "points": 1
            }
          ]
        }
      ]
    },
    "savedAnswers": [],                    // empty — fresh start
    "startedAt": "2026-05-12T10:00:00Z"
  }
}
```

#### Case B — active attempt exists **for the same exam** → **resume** (200)

**Response 200:**
```jsonc
{
  "status": 200,
  "data": {
    "attemptId": "uuid-of-existing",
    "isResume": true,                      // FE knows to show resume UI
    "exam": { /* same nested tree */ },
    "savedAnswers": [                      // previously saved via PATCH
      {"questionId": "uuid-q1", "studentAnswer": 2},
      {"questionId": "uuid-q5", "studentAnswer": "nine"}
    ],
    "startedAt": "2026-05-11T14:00:00Z"   // original start time
  }
}
```

`savedAnswers` contains only questions the student has previously saved.
Unsaved questions are absent. FE uses this list to prefill inputs.

`correct_index` / `correct_answers` / `correct_pairs` remain **stripped**
from `questionData` (mid-attempt rule unchanged).

#### Case C — active attempt exists **for a different exam** → **conflict** (409)

**Response 409:**
```jsonc
{
  "detail": "You have an unfinished attempt for another exam",
  "activeAttempt": {
    "attemptId": "uuid-of-in-progress",
    "examId": "uuid-of-other-exam",
    "examTitle": "KET Listening 03",
    "startedAt": "2026-05-11T14:00:00Z"
  }
}
```

FE shows a dialog:
> "Bạn đang làm dở bài **KET Listening 03**.
> [Tiếp tục bài cũ] [Bỏ bài cũ, làm bài mới]"

- **"Tiếp tục bài cũ"** → `POST /api/attempts {examId: old-exam-id}` (Case B).
- **"Bỏ bài cũ"** → `POST /api/attempts/{old-id}/abandon` → then retry `POST /api/attempts {examId: new-exam-id}` (Case A).

**Errors:**

| Status | Detail | Cause |
|---|---|---|
| 403 | `Parents cannot start exam attempts` | role = parent |
| 403 | `Monthly attempt limit reached (N)` | tier cap exceeded |
| 404 | `Exam {id} not found` | exam doesn't exist or is unpublished |

---

### 4.2 Get active attempt

```
GET /api/attempts/active
```

Returns the student's current in-progress attempt (if any). Used by FE on
app load or when entering the "Take Exam" page to show a "you have an
unfinished exam" banner.

**Response 200** (has active):
```jsonc
{
  "status": 200,
  "data": {
    "attemptId": "uuid",
    "examId": "uuid",
    "examTitle": "KET Reading 01",
    "examLevel": "KET",
    "examSkill": "reading",
    "startedAt": "2026-05-11T14:00:00Z",
    "savedAnswerCount": 12                 // how many answers already saved
  }
}
```

**Response 404** (no active):
```json
{
  "detail": "No active attempt"
}
```

---

### 4.3 Save answers (manual save)

```
PATCH /api/attempts/{attempt_id}/answers
```

Persists the student's current answers to the server without grading.
Called when student clicks **"💾 Lưu tạm"** button.

**Request:**
```json
{
  "answers": [
    {"questionId": "uuid-q1", "studentAnswer": 2},
    {"questionId": "uuid-q2", "studentAnswer": "nine"},
    {"questionId": "uuid-q5", "studentAnswer": 0}
  ]
}
```

- Sends **all current answers** (not just diff). Idempotent — server
  UPSERTs each row.
- `studentAnswer` follows the same type convention as submit:
  `int` for MC/matching, `string` for fill_blank, `null` for skip.
- Questions not included in the list are left as-is on the server (not
  deleted). To clear an answer, send `"studentAnswer": null`.

**Server behavior:**

For each answer in the list:
1. Validate `questionId` belongs to this attempt's exam.
2. UPSERT into `answers` table:
   - `is_correct = NULL` (not yet graded)
   - `points_earned = 0`
   - `student_answer = $value::jsonb`
3. If the `answers` row already exists (from a previous save), overwrite
   `student_answer`.

**Response 200:**
```json
{
  "status": 200,
  "data": {
    "savedCount": 3,
    "totalQuestions": 35,
    "message": "Answers saved"
  }
}
```

**Errors:**

| Status | Detail | Cause |
|---|---|---|
| 403 | `Not the owner of this attempt` | wrong user |
| 400 | `Attempt already submitted` | can't save after submit |
| 400 | `Attempt is abandoned` | can't save after abandon |
| 404 | `Attempt not found` | bad attempt_id |

---

### 4.4 Submit attempt

```
POST /api/attempts/{attempt_id}/submit
```

Finalizes the attempt. Grades all questions, computes score, locks the
attempt.

**Request:**
```json
{
  "answers": [
    {"questionId": "uuid-q1", "studentAnswer": 2},
    {"questionId": "uuid-q2", "studentAnswer": "nine"}
  ],
  "timeSpentSeconds": 1800
}
```

**Changed behavior (vs current):**
- If the student previously saved answers via `PATCH /answers`, submit
  **merges**: answers in the submit body override saved ones; saved
  answers not in the submit body are kept and graded as-is.
- Submit body can be empty `{"answers": []}` → grades only previously
  saved answers.

**Server behavior:**

1. Owner + not-submitted + not-abandoned checks.
2. Merge: build a map `{questionId → studentAnswer}` from saved DB rows,
   then overlay with body answers.
3. Grade each question against the merged map.
4. UPSERT `answers` rows with `is_correct` and `points_earned`.
5. Update `attempts` with `score`, `total_points`, `percentage`,
   `time_spent_seconds`, `submitted_at = now()`.

**Response 200:**
```json
{
  "status": 200,
  "data": {
    "score": 8.5,
    "totalPoints": 10,
    "percentage": 85.0,
    "submittedAt": "2026-05-12T10:30:00Z"
  }
}
```

**Errors:**

| Status | Detail | Cause |
|---|---|---|
| 403 | `Not the owner of this attempt` | wrong user |
| 400 | `Attempt already submitted` | double submit |
| 400 | `Attempt is abandoned` | can't submit abandoned |
| 404 | `Attempt not found` | bad attempt_id |

---

### 4.5 Abandon attempt

```
POST /api/attempts/{attempt_id}/abandon
```

Permanently marks the attempt as abandoned. Score = 0. The attempt
**still counts** toward the monthly quota (prevents abuse).

After abandoning, the student is free to start a new attempt (the
"1 active globally" slot is released).

**Request:** no body required.

**Response 200:**
```json
{
  "status": 200,
  "data": {
    "message": "Attempt abandoned",
    "attemptId": "uuid"
  }
}
```

**Server behavior:**

```sql
UPDATE public.attempts
SET is_abandoned = true,
    submitted_at = now(),
    score = 0,
    total_points = 0,
    percentage = 0
WHERE id = $1
```

**Errors:**

| Status | Detail | Cause |
|---|---|---|
| 403 | `Not the owner of this attempt` | wrong user |
| 400 | `Attempt already submitted` | already finalized |
| 400 | `Attempt already abandoned` | already abandoned |
| 404 | `Attempt not found` | bad attempt_id |

---

### 4.6 Audio play (per-material counter)

```
POST /api/attempts/{attempt_id}/sections/{section_id}/audio-play?materialIndex=N
```

**Unchanged from current spec.** Each audio material has its own counter;
cap is `sections.max_audio_plays`. See
[`FRONTEND_EXAM_GUIDE.md`](FRONTEND_EXAM_GUIDE.md) §11 for full
documentation.

**Additional check (new):** if `is_abandoned = true` → `400 "Attempt is abandoned"`.

---

### 4.7 Get attempt detail

```
GET /api/attempts/{attempt_id}
```

**Unchanged behavior.** Returns `attempt`, `exam` metadata, `answers[]`
grouped by section. Mid-attempt: correct fields stripped. Post-submit:
correct fields visible.

**New field in attempt view:** `isAbandoned: boolean`.

**Visibility rules (unchanged):**
- Owner can always view.
- Admin / teacher can view any attempt.
- Parent can view linked child's attempts.

---

### 4.8 Get attempt history

```
GET /api/attempts/history
```

**Minor change:** response items now include `isAbandoned` field so FE
can show "Abandoned" badge vs "Completed" in the history list.

```jsonc
{
  "status": 200,
  "data": {
    "items": [
      {
        "id": "uuid",
        "examId": "uuid",
        "examTitle": "KET Reading 01",
        "examLevel": "KET",
        "examSkill": "reading",
        "score": 0,
        "totalPoints": 10,
        "percentage": 0,
        "isAbandoned": true,               // NEW
        "timeSpentSeconds": null,
        "startedAt": "2026-05-12T10:00:00Z",
        "submittedAt": "2026-05-12T10:05:00Z"
      }
    ]
  }
}
```

---

## 5. Business rules

### 5.1 Quota

| Rule | Detail |
|------|--------|
| When is quota consumed? | On `201 Created` (genuine new attempt). Resume (200) does NOT consume. |
| Does abandon refund quota? | **No**. Abandoned attempts count toward monthly limit. |
| Does abandoned + started same exam = 2 quota? | **Yes**. Each `201 Created` response = 1 quota consumed, regardless of eventual outcome. |
| Tier limits | Free = 5/month, Basic = 50/month, Pro/Ultra = unlimited. |
| Counter query | `SELECT COUNT(*) FROM attempts WHERE user_id = $1 AND started_at >= subscription.current_period_start` (includes abandoned). |

### 5.2 Concurrency (race conditions)

The partial unique index `attempts_one_active_per_user` prevents two
concurrent `POST /api/attempts` from creating two active attempts. The
second INSERT fails with `UniqueViolationError` → service catches and
returns the existing active attempt (same as Case B).

### 5.3 Exam lifecycle interactions

| Scenario | Behavior |
|----------|----------|
| Admin unpublishes exam while student has active attempt | Student can still submit/save (attempt row already exists). Cannot start NEW attempt for that exam. |
| Admin soft-deletes exam while student has active attempt | Same as unpublish — active attempt is unaffected. Submit grades against existing questions. |
| Admin edits questions while student has active attempt | Mid-attempt save uses question IDs (stable). Submit grades against **current** question state (not snapshot). If question was hard-deleted, its answer row is silently skipped. |

### 5.4 Session timeout (not implemented in v1)

No auto-expiry. A student can leave an attempt open for days and resume
later. If auto-timeout becomes necessary, add a background job that
marks attempts as abandoned after N hours of inactivity (last save time
or started_at if never saved).

---

## 6. Frontend integration guide

### 6.1 App load — check for unfinished exam

```ts
// On app load or when entering "Take Exam" page:
try {
  const active = await api<ActiveAttempt>('/api/attempts/active');
  // Show banner: "Bạn đang làm dở bài [active.examTitle]. [Tiếp tục] [Bỏ]"
} catch (e) {
  if (e.status === 404) { /* no active attempt — show exam list normally */ }
}
```

### 6.2 Start / resume attempt

```ts
const res = await api<AttemptStart>('/api/attempts', {
  method: 'POST',
  body: JSON.stringify({examId}),
});

if (res.isResume) {
  // Prefill inputs from res.savedAnswers
  for (const sa of res.savedAnswers) {
    setAnswer(sa.questionId, sa.studentAnswer);
  }
  showToast('Tiếp tục bài làm dở');
} else {
  // Fresh start
  showToast('Bắt đầu làm bài');
}
```

**409 handling:**
```ts
try {
  await api('/api/attempts', { method: 'POST', body: ... });
} catch (e) {
  if (e.status === 409) {
    const {activeAttempt} = e.body;
    const choice = await showDialog(
      `Bạn đang làm dở bài "${activeAttempt.examTitle}"`,
      ['Tiếp tục bài cũ', 'Bỏ bài cũ, làm bài mới'],
    );
    if (choice === 0) {
      // Resume old
      router.push(`/exam/${activeAttempt.examId}`);
    } else {
      // Abandon old, then retry
      await api(`/api/attempts/${activeAttempt.attemptId}/abandon`, {method: 'POST'});
      // Retry start for new exam
      await api('/api/attempts', { method: 'POST', body: ... });
    }
  }
}
```

### 6.3 Save button

```tsx
function SaveButton({attemptId, answers}: {attemptId: string; answers: AnswerMap}) {
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const allAnswers = Object.entries(answers).map(([qid, sa]) => ({
        questionId: qid,
        studentAnswer: sa,
      }));
      await api(`/api/attempts/${attemptId}/answers`, {
        method: 'PATCH',
        body: JSON.stringify({answers: allAnswers}),
      });
      showToast('Đã lưu bài làm');
    } catch (e) {
      showToast('Lưu thất bại — thử lại');
    } finally {
      setSaving(false);
    }
  }

  return <button onClick={handleSave} disabled={saving}>
    {saving ? 'Đang lưu...' : '💾 Lưu tạm'}
  </button>;
}
```

**Recommended: warn before leaving**
```ts
useEffect(() => {
  const handler = (e: BeforeUnloadEvent) => {
    if (hasUnsavedChanges) {
      e.preventDefault();
      e.returnValue = '';  // browser shows native "Leave page?" dialog
    }
  };
  window.addEventListener('beforeunload', handler);
  return () => window.removeEventListener('beforeunload', handler);
}, [hasUnsavedChanges]);
```

### 6.4 Submit

```ts
const result = await api<SubmitResult>(`/api/attempts/${attemptId}/submit`, {
  method: 'POST',
  body: JSON.stringify({
    answers: currentAnswers,     // override any previously saved
    timeSpentSeconds: elapsed,
  }),
});
// result = {score, totalPoints, percentage, submittedAt}
router.push(`/results/${attemptId}`);
```

### 6.5 History list

```tsx
function AttemptHistoryItem({item}: {item: HistoryItem}) {
  return (
    <div>
      <span>{item.examTitle}</span>
      {item.isAbandoned
        ? <Badge color="gray">Đã bỏ</Badge>
        : <Badge color="green">{item.percentage}%</Badge>}
      <span>{formatDate(item.submittedAt)}</span>
    </div>
  );
}
```

---

## 7. Edge cases & decisions

| # | Scenario | Decision |
|---|----------|----------|
| 1 | Student F5 mid-exam without saving | `POST /attempts` returns existing attempt (200). Answers not saved → inputs empty. Student must re-answer. This is **by design** — save is explicit. FE should prompt `beforeunload`. |
| 2 | Student starts exam on phone, saves, opens on laptop | Resume works — server has saved answers. `POST /attempts {same examId}` returns 200 + `savedAnswers`. |
| 3 | Student abandons, then starts same exam again | New attempt created (201). Quota = +1 (total 2 for this exam in the period). Old attempt stays in history as "Abandoned". |
| 4 | Student at Free tier (5/month) starts and abandons 5 times | Tier limit reached. 6th `POST /attempts` returns 403. Intentional — prevents abuse. |
| 5 | Two tabs: student opens exam in tab A and tab B | Both tabs call `POST /attempts` — both get same attempt (idempotent). Both can save. Last save wins (UPSERT). Submit from either tab finalizes. No conflict. |
| 6 | Admin edits exam while student has active attempt | Student's attempt is not affected. Submit grades against current question state. If admin hard-deletes a question the student saved an answer for, that answer row becomes orphaned but harmless (CASCADE will clean up on hard-delete). |
| 7 | Student has active attempt, tier gets downgraded | No effect on the existing attempt. Tier limit only checked at `POST /attempts` (start). |
| 8 | Connectivity drops mid-save (PATCH /answers) | FE gets network error. Save did not persist. Student should retry. UPSERT is idempotent — retrying is safe. |
| 9 | What if `beforeunload` fires but PATCH fails (browser kills request)? | Answers lost since server didn't receive them. That's the trade-off of manual save. FE can mitigate by also saving to `localStorage` as backup and re-submitting on next load (before calling `POST /attempts`). |
| 10 | Parent role calls `POST /attempts` | 403 "Parents cannot start exam attempts" — unchanged. |

---

## Appendix: Summary of new/changed endpoints

| Method | Path | Change | Status codes |
|---|---|---|---|
| `POST`  | `/api/attempts` | **Modified** — idempotent; 3 cases (201/200/409) | 200, 201, 403, 404, 409 |
| `GET`   | `/api/attempts/active` | **New** — get current in-progress attempt | 200, 404 |
| `PATCH` | `/api/attempts/{id}/answers` | **New** — manual save (UPSERT answers) | 200, 400, 403, 404 |
| `POST`  | `/api/attempts/{id}/submit` | **Modified** — merge with saved answers | 200, 400, 403, 404 |
| `POST`  | `/api/attempts/{id}/abandon` | **New** — mark abandoned, release slot | 200, 400, 403, 404 |
| `GET`   | `/api/attempts/{id}` | **Modified** — add `isAbandoned` field | 200, 403, 404 |
| `GET`   | `/api/attempts/history` | **Modified** — add `isAbandoned` field | 200 |

**No changes to:** audio-play, parent endpoints, admin endpoints.
