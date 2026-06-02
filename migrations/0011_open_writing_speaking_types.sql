-- migrations/0011_open_writing_speaking_types.sql
--
-- Opens 'writing' and 'speaking' as valid values for:
--   - sections.type       (FE rendering hint)
--   - questions.question_type
--
-- This is the "open the slot" step only — it does NOT add:
--   - Per-type question_data validators in the service layer
--     (writing/speaking accept any shape with `prompt` for now;
--      stricter validation lands with the manual-grading feature).
--   - The writing_comments table or speaking_comment columns
--     (deferred per WRITING_SPEAKING.md §12).
--   - The is_fully_graded flag on attempts (deferred).
--   - The manual-grading endpoint (deferred).
--
-- Submit behavior with these types until the manual-grading feature
-- ships:
--   - `grade_question()` returns False for unknown types →
--     writing/speaking answers grade as 0 points / is_correct=false.
--   - `strip_correct()` is a no-op for these types → safe (their
--     question_data carries no correct_* fields anyway).
--
-- Idempotent.

-- 1. sections.type CHECK — extend with 'writing', 'speaking'.
ALTER TABLE public.sections
  DROP CONSTRAINT IF EXISTS sections_type_check;
ALTER TABLE public.sections
  ADD CONSTRAINT sections_type_check
  CHECK (
    type IS NULL
    OR type IN (
      'multiple_choice', 'multiple_choice_shared',
      'fill_blank', 'matching',
      'writing', 'speaking'
    )
  );

-- 2. questions.question_type CHECK — extend with 'writing', 'speaking'.
ALTER TABLE public.questions
  DROP CONSTRAINT IF EXISTS questions_question_type_check;
ALTER TABLE public.questions
  ADD CONSTRAINT questions_question_type_check
  CHECK (
    question_type IN (
      'multiple_choice', 'fill_blank', 'matching',
      'writing', 'speaking'
    )
  );
