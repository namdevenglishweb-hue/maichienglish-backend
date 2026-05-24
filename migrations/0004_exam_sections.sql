-- ============================================================
-- Migration 0004 — introduce `sections` layer between exams and questions.
-- Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3.
--
-- Layout: Exam → Section → Question. KET/PET-style "Part 1 / Part 2 / ..."
-- live as sections. Passage/audio/replay-cap move from exam to section.
-- Audio replay counters move from `attempts` to a new
-- `attempt_section_state` table so each section is tracked independently
-- (and so students can resume per section).
--
-- Breaking change. Drops columns + data on `exams`, `questions`, `attempts`.
-- Safe to run against the dev DB; production has not launched.
-- NOT idempotent on data (DROP COLUMN), but idempotent on schema objects
-- (uses IF [NOT] EXISTS where supported).
-- ============================================================

-- ------------------------------------------------------------
-- 1. sections — one row per "Part" within an exam
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sections (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  exam_id           uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  position          int  NOT NULL,
  part_label        text,                                  -- e.g. "Part 1"
  instructions      text,                                  -- rubric shown to student
  materials         jsonb NOT NULL DEFAULT '[]'::jsonb,    -- [{type:"text", label?, content}, ...]
  audio_url         text,                                  -- listening-only
  max_audio_plays   int,                                   -- listening-only
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  UNIQUE (exam_id, position)
);

CREATE INDEX IF NOT EXISTS idx_sections_exam
  ON public.sections(exam_id)
  WHERE deleted_at IS NULL;


-- ------------------------------------------------------------
-- 2. questions — repoint from exams → sections
--    (Drop the old exam_id FK + position uniqueness, add section_id.)
-- ------------------------------------------------------------
ALTER TABLE public.questions
  DROP COLUMN IF EXISTS exam_id;

ALTER TABLE public.questions
  ADD COLUMN IF NOT EXISTS section_id uuid
    REFERENCES public.sections(id) ON DELETE CASCADE;

-- New uniqueness: position is per-section, used by `{{gap:N}}` markers in
-- materials. Existing rows (if any survived the drop above) won't have a
-- section_id, so we leave the NOT NULL + UNIQUE constraints to be added
-- after backfill. For greenfield dev they apply immediately.
ALTER TABLE public.questions
  ALTER COLUMN section_id SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'questions_section_id_position_key'
  ) THEN
    ALTER TABLE public.questions
      ADD CONSTRAINT questions_section_id_position_key UNIQUE (section_id, position);
  END IF;
END $$;


-- ------------------------------------------------------------
-- 3. exams — drop the now-misplaced passage/audio columns
-- ------------------------------------------------------------
ALTER TABLE public.exams DROP COLUMN IF EXISTS audio_url;
ALTER TABLE public.exams DROP COLUMN IF EXISTS passage;
ALTER TABLE public.exams DROP COLUMN IF EXISTS max_audio_plays;


-- ------------------------------------------------------------
-- 4. attempt_section_state — per-section audio play counter + resume
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.attempt_section_state (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id        uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  section_id        uuid NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
  audio_play_count  int  NOT NULL DEFAULT 0,
  started_at        timestamptz,
  submitted_at      timestamptz,
  UNIQUE (attempt_id, section_id)
);

CREATE INDEX IF NOT EXISTS idx_attempt_section_state_attempt
  ON public.attempt_section_state(attempt_id);


-- ------------------------------------------------------------
-- 5. attempts — remove the exam-level audio counter (now per section)
-- ------------------------------------------------------------
ALTER TABLE public.attempts DROP COLUMN IF EXISTS audio_play_count;


-- ------------------------------------------------------------
-- 6. RLS on the two new tables (defense-in-depth per DEPLOYMENT.md §3.1).
--    The other 7 tables should already have RLS enabled from the initial
--    install; ENABLE is idempotent so it's safe either way.
-- ------------------------------------------------------------
ALTER TABLE public.sections              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attempt_section_state ENABLE ROW LEVEL SECURITY;
