-- ============================================================
-- Migration 0008 — add 'multiple_choice_shared' to sections.type enum.
--
-- Rendering hint for KET Reading Part 2-style sections: multiple MC
-- questions sharing the same 3 options (e.g. A=Sandy Bay, B=High Wood,
-- C=Black Lake). FE renders as compact table with shared header.
-- Data shape unchanged (each question still uses MC shape).
-- Idempotent.
-- ============================================================

ALTER TABLE public.sections DROP CONSTRAINT IF EXISTS sections_type_check;
ALTER TABLE public.sections ADD CONSTRAINT sections_type_check
  CHECK (type IN ('multiple_choice', 'fill_blank', 'matching', 'multiple_choice_shared'));
