-- ============================================================
-- Migration 0017 — Attempt highlights (highlight + note khi làm bài).
-- Source of truth: docs/attempt-highlights/attempt-highlights-design.md §3
--
-- 1 row / highlight. `target_key` opaque với BE (FE↔BE convention) — BE
-- không parse, chỉ lưu/trả. Offset ký tự trên chuỗi nguồn của text-run.
-- Mutation chỉ owner + attempt in_progress (enforce ở service). Read đi
-- qua embed (resume/detail) — không có GET list riêng.
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.attempt_highlights (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id  uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
    target_key  text NOT NULL,
    range_start int  NOT NULL CHECK (range_start >= 0),
    range_end   int  NOT NULL,
    quoted_text text NOT NULL,
    note        text,
    color       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (range_end > range_start)
);

CREATE INDEX IF NOT EXISTS attempt_highlights_attempt_idx
  ON public.attempt_highlights (attempt_id);

ALTER TABLE public.attempt_highlights ENABLE ROW LEVEL SECURITY;
