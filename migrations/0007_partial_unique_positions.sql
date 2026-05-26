-- ============================================================
-- Migration 0007 — partial UNIQUE indexes on (exam_id, position) /
-- (section_id, position) so soft-deleted rows don't block position reuse.
--
-- Root cause for prod 500 on POST /sections:
--   sections.UNIQUE(exam_id, position) is a plain b-tree unique
--   constraint — it does NOT honor `deleted_at IS NULL`. So when admin
--   soft-deletes sections at positions 1, 3, 4, 5 and the service
--   auto-assigns next position = MAX(active.position)+1 = 3, the INSERT
--   collides with the soft-deleted row still occupying position 3 →
--   asyncpg.UniqueViolationError → uncaught → 500.
--
-- Fix: drop the table-level constraint, add a partial unique INDEX
-- filtered to active rows only. Soft-deleted rows no longer block reuse.
-- Same fix for questions.
-- Idempotent.
-- ============================================================

-- sections ----------------------------------------------------
ALTER TABLE public.sections
  DROP CONSTRAINT IF EXISTS sections_exam_id_position_key;

CREATE UNIQUE INDEX IF NOT EXISTS sections_exam_id_position_active_key
  ON public.sections (exam_id, position)
  WHERE deleted_at IS NULL;

-- questions ---------------------------------------------------
ALTER TABLE public.questions
  DROP CONSTRAINT IF EXISTS questions_section_id_position_key;

CREATE UNIQUE INDEX IF NOT EXISTS questions_section_id_position_active_key
  ON public.questions (section_id, position)
  WHERE deleted_at IS NULL;
