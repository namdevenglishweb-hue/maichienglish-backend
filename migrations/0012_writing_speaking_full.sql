-- migrations/0012_writing_speaking_full.sql
--
-- Full writing/speaking feature DB schema. Builds on 0011 (which
-- opened the question_type / section.type CHECK constraints).
--
-- Adds:
--   1. attempts.is_fully_graded boolean (DEFAULT true)
--   2. writing_comments table — range-based teacher annotations
--   3. answers.speaking_comment* — single overall comment columns
--
-- See WRITING_SPEAKING.md §11 + §9.2 for design rationale.
--
-- Idempotent.

-- ---------------------------------------------------------------------------
-- 1. attempts.is_fully_graded
--
-- DEFAULT true so existing attempts (auto-graded only) are correctly
-- marked. New attempts on exams with writing/speaking will be flipped
-- to false at submit time (see attempt_service.submit_attempt).
-- ---------------------------------------------------------------------------

ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS is_fully_graded boolean NOT NULL DEFAULT true;


-- ---------------------------------------------------------------------------
-- 2. writing_comments — range-based teacher annotations on writing answers.
--
-- Multiple comments per answer. Overlap is rejected at application
-- layer (with row lock on answers row) — see services/comment_service.py.
-- quoted_text stores the snapshot of student_answer.text[start:end] at
-- comment creation time for display + audit.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.writing_comments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id     uuid NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
  range_start   int  NOT NULL CHECK (range_start >= 0),
  range_end     int  NOT NULL CHECK (range_end > range_start),
  quoted_text   text NOT NULL,
  comment_text  text NOT NULL CHECK (length(comment_text) > 0),
  created_by    uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS writing_comments_answer_id_idx
  ON public.writing_comments (answer_id, range_start);


-- ---------------------------------------------------------------------------
-- 3. answers.speaking_comment* — single overall comment per speaking answer.
--
-- Inline columns (no separate table) because at most one row per answer.
-- PUT semantics: writes all three; DELETE clears all three to NULL.
-- ---------------------------------------------------------------------------

ALTER TABLE public.answers
  ADD COLUMN IF NOT EXISTS speaking_comment      text,
  ADD COLUMN IF NOT EXISTS speaking_comment_by   uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS speaking_comment_at   timestamptz;
