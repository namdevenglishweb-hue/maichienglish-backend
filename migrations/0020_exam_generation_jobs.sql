-- ============================================================
-- Migration 0020 — Async job table for AI exam generation (Phase 2).
-- Source of truth: docs/exam-ai-generation/exam-ai-generation-design.md §14
--
-- One row per generation task, driven by FastAPI BackgroundTasks; FE polls.
-- scope:
--   'exam'         (Mode 1) → whole-exam, all-or-nothing, auto-saves
--                              the draft into result_exam_id.
--   'section'      (Mode 2)  → single part; result section payload lives in
--                              report.sections[0]; NOT persisted as an exam.
--   'exam_preview' (Mode 2)  → all parts at once; report.sections[] +
--                              per-part status; NOT persisted.
-- target_section_id only for scope='section'.
-- Save (assemble) of a Mode 2 draft is a SEPARATE synchronous endpoint —
-- not a job.
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.exam_generation_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope             text NOT NULL DEFAULT 'exam'
                        CHECK (scope IN ('exam', 'section', 'exam_preview')),
    source_exam_id    uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    target_section_id uuid REFERENCES public.sections(id) ON DELETE CASCADE,
    k                 integer NOT NULL CHECK (k BETWEEN 1 AND 5),
    title             text,
    status            text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'aborted')),
    sections_total    integer,
    sections_done     integer NOT NULL DEFAULT 0,
    current_section   integer,
    result_exam_id    uuid REFERENCES public.exams(id) ON DELETE SET NULL,
    report            jsonb,
    aborted_reason    text,
    cancel_requested  boolean NOT NULL DEFAULT false,
    created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS exam_generation_jobs_status_idx
  ON public.exam_generation_jobs (status, created_at DESC);

ALTER TABLE public.exam_generation_jobs ENABLE ROW LEVEL SECURITY;
