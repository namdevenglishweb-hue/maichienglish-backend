-- ============================================================
-- Migration 0021 — Async job table for AI image generation.
-- Source of truth: docs/exam-image-generation/exam-image-generation-design.md §8
--
-- One row per single-image generation task (FE bắn N job cho N ảnh).
-- Driven by FastAPI BackgroundTasks; FE polls. Independent of exams —
-- the job only needs `description` (+ optional source image to edit).
--   mode: 'edit' (có source_image_url, sửa ảnh cũ — giữ layout/chữ) /
--         'generate' (vẽ mới from description).
--   succeeded → result_url (ảnh đã upload bucket `images`).
--   failed    → report.verifyReason (FE giữ pendingReplacement, làm tay).
-- No cancel (job ngắn). report is camelCase jsonb.
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.image_generation_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    description       text NOT NULL,
    source_image_url  text,
    mode              text NOT NULL DEFAULT 'generate'
                        CHECK (mode IN ('generate', 'edit')),
    status            text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    result_url        text,
    report            jsonb,
    created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS image_generation_jobs_status_idx
  ON public.image_generation_jobs (status, created_at DESC);

ALTER TABLE public.image_generation_jobs ENABLE ROW LEVEL SECURITY;
