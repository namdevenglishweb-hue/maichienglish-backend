-- ============================================================
-- Migration 0005 — section.type rendering hint + matching shape unification.
-- Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3.5 / §3.6.
--
-- Adds:
--   - sections.type — soft-hint enum for FE rendering layout.
--     'multiple_choice' / 'fill_blank' → vertical list.
--     'matching'                       → shared-options table.
--     NULL                             → mixed; FE per-question.
--
-- Behavior change (no DDL):
--   - `matching` question_data now shares MC shape ({stem, options, correct_index}).
--     Previous {left, right, correct_pairs} interpretation never reached prod;
--     no data migration is necessary.
--
-- Idempotent.
-- ============================================================

ALTER TABLE public.sections
  ADD COLUMN IF NOT EXISTS type text
    CHECK (type IN ('multiple_choice', 'fill_blank', 'matching'));
