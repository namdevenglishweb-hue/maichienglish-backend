-- ============================================================
-- Migration 0003 — add password_reset_codes table for B3.6a
-- Stores bcrypt-hashed reset codes (6-digit numeric); short-lived (10 min).
-- Idempotent.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.password_reset_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  code_hash   text NOT NULL,                              -- bcrypt hash of the code
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,                                -- set when the code is consumed
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_codes_user
  ON public.password_reset_codes(user_id);

CREATE INDEX IF NOT EXISTS idx_password_reset_codes_expires
  ON public.password_reset_codes(expires_at);
