-- ============================================================
-- Migration 0014 — add 'form_completion' to sections.type enum.
--
-- Rendering hint for KET Listening-style note/form completion: a
-- label–value form where each blank is its own question (no passage,
-- no {{gap:N}} markers). Each blank may carry inline prefix/postfix
-- text (e.g. "from ___ to 5 p.m.", "Mr ___").
--
-- Data shape is UNCHANGED: questions stay `question_type='fill_blank'`
-- (same string-match grading + strip). The form layout + per-row
-- label/prefix/postfix live in question_data as presentation-only
-- fields. So this migration only touches sections.type.
--
-- Idempotent. schema.sql updated lockstep.
-- ============================================================

ALTER TABLE public.sections DROP CONSTRAINT IF EXISTS sections_type_check;
ALTER TABLE public.sections ADD CONSTRAINT sections_type_check
  CHECK (type IN ('multiple_choice', 'fill_blank', 'matching',
                  'multiple_choice_shared', 'writing', 'speaking',
                  'form_completion'));
