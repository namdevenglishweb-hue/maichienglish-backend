-- ============================================================
-- Migration 0018 — Exam AI Generation provenance on exams.
-- Source of truth: docs/exam-ai-generation/exam-ai-generation-design.md §11
--
-- Adds two nullable columns to exams so an AI-generated exam can be
-- traced back to its source + carry an audit snapshot of the run:
--   generated_from_exam_id : FK→exams (the source exam this was cloned from)
--   generation_meta        : jsonb audit (k, model, section_prompts,
--                            media_todos, self_review, token_usage, retries)
--
-- Transcript/description of media live in sections.materials[].meta
-- (NOT here) — see §5. Both columns nullable ⇒ hand-made exams unaffected.
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

ALTER TABLE public.exams
  ADD COLUMN IF NOT EXISTS generated_from_exam_id uuid
    REFERENCES public.exams(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS generation_meta jsonb;
