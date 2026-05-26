-- ============================================================
-- Mai Chi English — initial database schema
-- Run once in Supabase SQL Editor on a fresh project (PostgreSQL 17).
-- Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3
-- ============================================================

-- gen_random_uuid() comes from pgcrypto; usually pre-installed on Supabase.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ------------------------------------------------------------
-- profiles — users + custom JWT auth (replaces Supabase Auth coupling)
-- ------------------------------------------------------------
CREATE TABLE public.profiles (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL UNIQUE,
  password_hash  text NOT NULL,
  full_name      text NOT NULL,
  phone          text,
  role           text NOT NULL DEFAULT 'student'
                  CHECK (role IN ('student', 'teacher', 'admin', 'parent')),
  parent_id      uuid REFERENCES public.profiles(id) ON DELETE SET NULL,  -- only set when role='student'
  created_at     timestamptz NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------
-- subscriptions — one active row per user
-- ------------------------------------------------------------
CREATE TABLE public.subscriptions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               uuid NOT NULL UNIQUE
                          REFERENCES public.profiles(id) ON DELETE CASCADE,
  tier                  text NOT NULL DEFAULT 'free'
                          CHECK (tier IN ('free', 'basic', 'pro', 'ultra')),
  status                text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'canceled', 'expired')),
  credits_monthly       int  NOT NULL DEFAULT 0,
  credits_remaining     int  NOT NULL DEFAULT 0,
  current_period_start  timestamptz NOT NULL DEFAULT now(),
  current_period_end    timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------
-- password_reset_codes — short-lived 6-digit codes for self-service reset
-- ------------------------------------------------------------
CREATE TABLE public.password_reset_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_password_reset_codes_user
  ON public.password_reset_codes(user_id);
CREATE INDEX idx_password_reset_codes_expires
  ON public.password_reset_codes(expires_at);


-- ------------------------------------------------------------
-- exams — top-level definition; passage/audio live on sections (§3.5)
-- ------------------------------------------------------------
CREATE TABLE public.exams (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title             text NOT NULL,
  level             text NOT NULL
                      CHECK (level IN ('primary', 'secondary', 'KET', 'PET', 'IELTS')),
  skill             text NOT NULL
                      CHECK (skill IN ('listening', 'reading')),
  duration_minutes  int  NOT NULL DEFAULT 45 CHECK (duration_minutes > 0),
  description       text,
  is_published      boolean NOT NULL DEFAULT false,
  created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz
);


-- ------------------------------------------------------------
-- sections — one row per "Part" of an exam (KET/PET style).
--   `materials` is a JSONB list of typed blocks shown above the questions:
--     - {type:"text",  label?, content}            (passage; supports {{gap:N}})
--     - {type:"image", label?, url, alt?}          (diagram, form, illustration)
--     - {type:"audio", label?, url}                (listening clip)
--   max_audio_plays is a SECTION-WIDE cap value that applies independently
--   to every audio material in this section (per-audio counter, shared cap).
-- ------------------------------------------------------------
CREATE TABLE public.sections (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  exam_id           uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  position          int  NOT NULL,
  part_label        text,
  type              text                                          -- FE rendering hint; soft
                      CHECK (type IN ('multiple_choice', 'fill_blank', 'matching')),
  instructions      text,
  materials         jsonb NOT NULL DEFAULT '[]'::jsonb,
  max_audio_plays   int,                                          -- cap value; null = unlimited
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  UNIQUE (exam_id, position)
);

CREATE INDEX idx_sections_exam
  ON public.sections(exam_id)
  WHERE deleted_at IS NULL;


-- ------------------------------------------------------------
-- questions — three types: multiple_choice, fill_blank, matching.
--   Scoped to a section. `position` is per-section and referenced by
--   {{gap:N}} markers inside sections.materials content.
--   multiple_choice options support text and/or image_url
--   (shared-options pattern is denormalized per question — see plan §3.5).
-- ------------------------------------------------------------
CREATE TABLE public.questions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  section_id     uuid NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
  position       int  NOT NULL,
  question_type  text NOT NULL
                  CHECK (question_type IN ('multiple_choice', 'fill_blank', 'matching')),
  question_data  jsonb NOT NULL,
  points         int  NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  UNIQUE (section_id, position)
);


-- ------------------------------------------------------------
-- attempts — one row per exam attempt. Per-section state (audio plays,
-- resume) lives on attempt_section_state.
-- ------------------------------------------------------------
CREATE TABLE public.attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id              uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score                numeric(6,2),
  total_points         numeric(6,2),
  percentage           numeric(5,2),
  time_spent_seconds   int,
  started_at           timestamptz NOT NULL DEFAULT now(),
  submitted_at         timestamptz
);


-- ------------------------------------------------------------
-- attempt_section_state — per-section progress for an attempt.
--   audio_play_counts: jsonb map {"<material_index>": <play_count>, ...}
--   Each audio material in the section has its own counter; all share
--   the same `sections.max_audio_plays` cap value. material_index is
--   positional within sections.materials JSONB (caveat documented in
--   FE GUIDE: admins should avoid reordering materials mid-attempt).
-- ------------------------------------------------------------
CREATE TABLE public.attempt_section_state (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id        uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  section_id        uuid NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
  audio_play_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at        timestamptz,
  submitted_at      timestamptz,
  UNIQUE (attempt_id, section_id)
);

CREATE INDEX idx_attempt_section_state_attempt
  ON public.attempt_section_state(attempt_id);


-- ------------------------------------------------------------
-- answers — one row per question per attempt; stores graded result
-- ------------------------------------------------------------
CREATE TABLE public.answers (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id      uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  question_id     uuid NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  student_answer  jsonb,
  is_correct      boolean,
  points_earned   int NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------
-- Row-level security. Defense-in-depth per DEPLOYMENT.md §3.1 / §8 —
-- the backend connects via the service-role key (which bypasses RLS),
-- but enabling RLS blocks bare anon/authenticated key holders from
-- ever reading these tables directly if a key ever leaks. Idempotent.
-- ------------------------------------------------------------
ALTER TABLE public.profiles              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.password_reset_codes  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exams                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sections              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.questions             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attempts              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attempt_section_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answers               ENABLE ROW LEVEL SECURITY;
