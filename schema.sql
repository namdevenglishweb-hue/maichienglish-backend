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
                  CHECK (role IN ('student', 'teacher', 'admin')),
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
-- exams — listening uses audio_url + max_audio_plays; reading uses passage
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
  audio_url         text,
  passage           text,
  max_audio_plays   int  NOT NULL DEFAULT 3,
  is_published      boolean NOT NULL DEFAULT false,
  created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz
);


-- ------------------------------------------------------------
-- questions — three types: multiple_choice, fill_blank, matching
-- ------------------------------------------------------------
CREATE TABLE public.questions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  exam_id        uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  position       int  NOT NULL,
  question_type  text NOT NULL
                  CHECK (question_type IN ('multiple_choice', 'fill_blank', 'matching')),
  question_data  jsonb NOT NULL,
  points         int  NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);


-- ------------------------------------------------------------
-- attempts — one row per exam attempt; tracks listening audio replays
-- ------------------------------------------------------------
CREATE TABLE public.attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id              uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score                numeric(6,2),
  total_points         numeric(6,2),
  percentage           numeric(5,2),
  time_spent_seconds   int,
  audio_play_count     int NOT NULL DEFAULT 0,
  started_at           timestamptz NOT NULL DEFAULT now(),
  submitted_at         timestamptz
);


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
