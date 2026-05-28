-- ============================================================
-- Migration 0010 — Attempt lifecycle: is_abandoned flag,
--                  one-active-per-user index, and UPSERT key on answers.
--
-- Source of truth: ATTEMPT_LIFECYCLE.md §2.
--
-- 1. Add `attempts.is_abandoned` boolean (default false). Used by the
--    /abandon endpoint to finalize an attempt with score=0 while freeing
--    the "1 active attempt globally" slot.
--
-- 2. Backfill: collapse any pre-existing duplicate active attempts per
--    user (older ones marked abandoned). Required before step 3 because
--    the partial unique index would otherwise fail on existing data.
--
-- 3. Partial unique index on attempts: at most ONE active (= not
--    submitted, not abandoned) attempt per user. Race-condition safe —
--    a concurrent INSERT fails at the DB layer; the service catches
--    `UniqueViolationError` and resolves to either Case B (200 resume,
--    same exam) or Case C (409 conflict, different exam).
--
-- 4. Add `UNIQUE (attempt_id, question_id)` on answers so PATCH /answers
--    (save mid-attempt) can ON CONFLICT DO UPDATE. Submit also UPSERTs
--    via this key to grade-overwrite a previously-saved answer.
--
-- Idempotent.
-- ============================================================

-- 1. is_abandoned column ----------------------------------------
ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS is_abandoned boolean NOT NULL DEFAULT false;

-- 2. Clean up pre-existing duplicate active attempts. ------------
-- Before this constraint shipped, the service freely created multiple
-- in-progress attempts per user. The partial unique index below would
-- reject creation while those duplicates exist. Keep the MOST RECENT
-- active attempt per user and mark the older ones as abandoned (score=0,
-- submitted_at=now()) — they'll appear in history as "Đã bỏ".
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY user_id
           ORDER BY started_at DESC, id DESC
         ) AS rn
  FROM public.attempts
  WHERE submitted_at IS NULL AND NOT is_abandoned
)
UPDATE public.attempts a
SET is_abandoned = true,
    submitted_at = now(),
    score = 0,
    total_points = 0,
    percentage = 0
FROM ranked r
WHERE a.id = r.id AND r.rn > 1;

-- 3. one-active-per-user partial unique index -------------------
CREATE UNIQUE INDEX IF NOT EXISTS attempts_one_active_per_user
  ON public.attempts (user_id)
  WHERE submitted_at IS NULL AND NOT is_abandoned;

-- 4. answers UPSERT key -----------------------------------------
-- Before this migration, there was no unique constraint on
-- (attempt_id, question_id). Existing dev DBs may contain duplicate
-- rows from earlier non-UPSERT submits; collapse them here keeping
-- the most recent row per (attempt, question).
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY attempt_id, question_id
           ORDER BY created_at DESC, id DESC
         ) AS rn
  FROM public.answers
)
DELETE FROM public.answers
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

CREATE UNIQUE INDEX IF NOT EXISTS answers_attempt_question_key
  ON public.answers (attempt_id, question_id);
