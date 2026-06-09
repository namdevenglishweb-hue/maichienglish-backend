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
  deleted_at        timestamptz,
  -- AI exam generation provenance (migration 0018, docs/exam-ai-generation §11).
  generated_from_exam_id uuid REFERENCES public.exams(id) ON DELETE SET NULL,
  generation_meta        jsonb
);


-- ------------------------------------------------------------
-- sections — one row per "Part" of an exam (KET/PET style).
--   audio/image materials may carry admin-only `meta`
--   ({transcript|description, pendingReplacement}) for AI generation
--   (docs/exam-ai-generation §5) — stripped from student payloads.
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
                      CHECK (type IN ('multiple_choice', 'fill_blank', 'matching', 'multiple_choice_shared', 'writing', 'speaking', 'form_completion')),
  instructions      text,
  materials         jsonb NOT NULL DEFAULT '[]'::jsonb,
  max_audio_plays   int,                                          -- cap value; null = unlimited
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz
);

-- Partial unique: only ACTIVE sections must have distinct positions per
-- exam. Soft-deleted rows keep their old position without blocking reuse.
CREATE UNIQUE INDEX sections_exam_id_position_active_key
  ON public.sections (exam_id, position)
  WHERE deleted_at IS NULL;

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
                  CHECK (question_type IN ('multiple_choice', 'fill_blank', 'matching', 'writing', 'speaking')),
  question_data  jsonb NOT NULL,
  points         int  NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);

-- Partial unique: only ACTIVE questions must have distinct positions per
-- section. Soft-deleted rows don't block reuse (same fix as sections).
CREATE UNIQUE INDEX questions_section_id_position_active_key
  ON public.questions (section_id, position)
  WHERE deleted_at IS NULL;


-- ------------------------------------------------------------
-- attempts — one row per exam attempt. Per-section state (audio plays,
-- resume) lives on attempt_section_state.
--   `is_abandoned` (set by POST /attempts/{id}/abandon) finalizes the
--   attempt with score=0 while freeing the "1 active globally" slot.
--   The partial unique index below enforces that rule at the DB layer.
-- ------------------------------------------------------------
CREATE TABLE public.attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id              uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score                numeric(6,2),
  total_points         numeric(6,2),
  percentage           numeric(5,2),
  time_spent_seconds   int,
  is_abandoned         boolean NOT NULL DEFAULT false,
  is_fully_graded      boolean NOT NULL DEFAULT true,
  -- exam mode (migration 0016): 'real' (thi thật) forces audio plays to 1
  -- per audio + no-resume; 'practice' (default) = current behaviour.
  mode                 text NOT NULL DEFAULT 'practice'
                         CHECK (mode IN ('practice','real')),
  started_at           timestamptz NOT NULL DEFAULT now(),
  submitted_at         timestamptz
);

-- At most ONE active (= not submitted, not abandoned) attempt per user.
-- Race-condition safe: concurrent INSERTs hit the index and one fails.
CREATE UNIQUE INDEX attempts_one_active_per_user
  ON public.attempts (user_id)
  WHERE submitted_at IS NULL AND NOT is_abandoned;


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
-- answers — one row per question per attempt; stores graded result.
--   UNIQUE (attempt_id, question_id) enables UPSERT semantics used by
--   PATCH /attempts/{id}/answers (mid-attempt save, is_correct=NULL)
--   and POST /attempts/{id}/submit (overwrite with graded values).
-- ------------------------------------------------------------
CREATE TABLE public.answers (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id            uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  question_id           uuid NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  student_answer        jsonb,
  is_correct            boolean,
  points_earned         int NOT NULL DEFAULT 0,
  -- speaking-overall comment (writing comments use writing_comments table)
  speaking_comment      text,
  speaking_comment_by   uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  speaking_comment_at   timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (attempt_id, question_id)
);


-- ------------------------------------------------------------
-- writing_comments — range-based teacher annotations on writing answers.
--   Multiple comments per answer. Overlap rejected at application layer
--   (with row lock on answers row) — see services/comment_service.py.
--   quoted_text stores the snapshot of student_answer.text[start:end]
--   at comment creation time for display + audit.
-- ------------------------------------------------------------
CREATE TABLE public.writing_comments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id     uuid NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
  range_start   int  NOT NULL CHECK (range_start >= 0),
  range_end     int  NOT NULL CHECK (range_end > range_start),
  quoted_text   text NOT NULL,
  comment_text  text NOT NULL CHECK (length(comment_text) > 0),
  created_by    uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX writing_comments_answer_id_idx
  ON public.writing_comments (answer_id, range_start);


-- ------------------------------------------------------------
-- classes / class_teachers / class_students — grouping students +
-- teachers into classes for teacher-grading scoping (migration 0013).
--   class_teachers is N-N (a class has 1+ teachers; a teacher teaches
--   many classes). class_students enforces 1-class-per-student via
--   UNIQUE(student_id). Membership FKs CASCADE on classes/profiles
--   delete (housekeeping); the "delete class only when empty" rule is
--   enforced at the application layer — see services/class_service.py.
-- ------------------------------------------------------------
CREATE TABLE public.classes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.class_teachers (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  teacher_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, teacher_id)
);
CREATE INDEX class_teachers_teacher_idx
  ON public.class_teachers (teacher_id);

CREATE TABLE public.class_students (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, student_id)
  -- v2 (migration 0015): student có thể thuộc NHIỀU lớp; PK vẫn chặn
  -- thêm trùng cùng lớp.
);
CREATE INDEX class_students_class_idx
  ON public.class_students (class_id);


-- ------------------------------------------------------------
-- attempt_highlights — student highlight + optional note while taking an
-- attempt (migration 0017). One row per highlight.
--   target_key: opaque text run locator (FE↔BE convention; BE never
--   parses it) — e.g. "material:{sectionId}:{idx}:content",
--   "question:{questionId}:stem", "answer:{questionId}". range_start/end
--   are char offsets on that run's source string; quoted_text is a
--   snapshot. Mutation = owner + attempt in_progress (app layer); read via
--   embed in resume/detail. Overlap allowed. See
--   services/highlight_service.py + docs/attempt-highlights/.
-- ------------------------------------------------------------
CREATE TABLE public.attempt_highlights (
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
CREATE INDEX attempt_highlights_attempt_idx
  ON public.attempt_highlights (attempt_id);


-- ------------------------------------------------------------
-- section_type_prompts — admin per-type prompt config (source A) for AI
--   exam generation. One row per section type; injected at generation time
--   below the structure/quality invariants. See docs/exam-ai-generation §10.
-- ------------------------------------------------------------
CREATE TABLE public.section_type_prompts (
  type              text PRIMARY KEY
                      CHECK (type IN ('multiple_choice', 'multiple_choice_shared',
                                      'fill_blank', 'matching', 'writing', 'speaking',
                                      'form_completion')),
  additional_prompt text NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL
);


-- ------------------------------------------------------------
-- exam_generation_jobs — async job tracking for AI exam generation (Phase 2).
--   scope: 'exam' (Mode 1, auto-saves result_exam_id) / 'section' (Mode 2
--   single) / 'exam_preview' (Mode 2 all) — section/preview return their
--   generated section payloads in report.sections[] (not persisted as exams).
--   See docs/exam-ai-generation §14.
-- ------------------------------------------------------------
CREATE TABLE public.exam_generation_jobs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope             text NOT NULL DEFAULT 'exam'
                      CHECK (scope IN ('exam', 'section', 'exam_preview')),
  source_exam_id    uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  target_section_id uuid REFERENCES public.sections(id) ON DELETE CASCADE,
  k                 int  NOT NULL CHECK (k BETWEEN 1 AND 5),
  title             text,
  status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'aborted')),
  sections_total    int,
  sections_done     int  NOT NULL DEFAULT 0,
  current_section   int,
  result_exam_id    uuid REFERENCES public.exams(id) ON DELETE SET NULL,
  report            jsonb,
  aborted_reason    text,
  cancel_requested  boolean NOT NULL DEFAULT false,
  created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz
);
CREATE INDEX exam_generation_jobs_status_idx
  ON public.exam_generation_jobs (status, created_at DESC);


-- ------------------------------------------------------------
-- image_generation_jobs — async single-image generation (Nano Banana /
--   Gemini 2.5 Flash Image). FE bắn N job cho N ảnh; mỗi job 1 ảnh.
--   mode edit (sửa source_image_url) | generate (vẽ mới). succeeded →
--   result_url (bucket images); failed → report.verifyReason (manual).
--   Independent of exams. See docs/exam-image-generation/ §8.
-- ------------------------------------------------------------
CREATE TABLE public.image_generation_jobs (
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
CREATE INDEX image_generation_jobs_status_idx
  ON public.image_generation_jobs (status, created_at DESC);


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
ALTER TABLE public.writing_comments      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.classes               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_teachers        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_students        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attempt_highlights    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.section_type_prompts  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_generation_jobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.image_generation_jobs ENABLE ROW LEVEL SECURITY;
