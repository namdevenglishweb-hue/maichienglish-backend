-- ============================================================
-- Migration 0002 — add `parent` role + `profiles.parent_id` column
-- Run once in Supabase SQL Editor. Idempotent (re-runnable).
-- ============================================================

-- 1. Add parent_id self-FK. Only meaningful for students.
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS parent_id uuid
    REFERENCES public.profiles(id) ON DELETE SET NULL;

-- 2. Replace the role CHECK constraint to include 'parent'.
--    The original CHECK was inline so its name is auto-generated; look it
--    up dynamically before dropping.
DO $$
DECLARE
  cons_name text;
BEGIN
  SELECT conname INTO cons_name
  FROM pg_constraint
  WHERE conrelid = 'public.profiles'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%role%';

  IF cons_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.profiles DROP CONSTRAINT %I', cons_name);
  END IF;
END$$;

ALTER TABLE public.profiles
  ADD CONSTRAINT profiles_role_check
  CHECK (role IN ('student', 'teacher', 'admin', 'parent'));
