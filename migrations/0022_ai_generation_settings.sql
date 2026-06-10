-- ============================================================
-- Migration 0022 — Runtime-editable AI generation settings (no redeploy).
--
-- A singleton row holding the AI generation defaults that used to live only
-- in env vars (AI_PROVIDER / AI_MODEL / AI_MAX_TOKENS / AI_SELF_REVIEW_ROUNDS).
-- Admin edits these via the API so trying a different model / turning down
-- self-review (e.g. for Gemini) no longer needs an env change + redeploy.
--
-- Resolution order at generation time: per-request override > this row > env.
-- Each column is NULLABLE: NULL means "fall back to the env default".
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.ai_generation_settings (
    id                 smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    provider           text,
    model              text,
    max_tokens         integer CHECK (max_tokens IS NULL OR max_tokens > 0),
    self_review_rounds integer CHECK (self_review_rounds IS NULL OR self_review_rounds >= 0),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by         uuid REFERENCES public.profiles(id) ON DELETE SET NULL
);

ALTER TABLE public.ai_generation_settings ENABLE ROW LEVEL SECURITY;
