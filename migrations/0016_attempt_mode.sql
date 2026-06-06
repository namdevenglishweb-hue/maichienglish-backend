-- ============================================================
-- Migration 0016 — Exam mode (thi thử / thi thật) on attempts.
-- Source of truth: docs/exam-mode/exam-mode-design.md §3
--
-- Adds attempts.mode:
--   'practice' (default) = current behaviour (thi thử).
--   'real'     (thi thật) = audio max_audio_plays forced to 1 per audio +
--                            no-resume (interrupted real attempt is abandoned;
--                            enforced in the service start flow).
--
-- Additive/idempotent. Existing rows + clients that omit mode → 'practice'
-- (fully backward compatible). schema.sql updated lockstep.
-- ============================================================

ALTER TABLE public.attempts
  ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'practice'
    CHECK (mode IN ('practice','real'));
