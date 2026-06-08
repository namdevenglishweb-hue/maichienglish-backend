-- ============================================================
-- Migration 0013 — Class management (foundation).
-- Source of truth: docs/class-management/class-management-design.md §3
--
-- Adds three tables for grouping students + teachers into classes:
--   classes         — the class itself (name + description)
--   class_teachers  — N-N: a class has 1+ teachers, a teacher dạy nhiều lớp
--   class_students  — 1-class-per-student (enforced by UNIQUE(student_id))
--
-- Additive + idempotent (IF NOT EXISTS). Membership FKs CASCADE on
-- classes/profiles delete (housekeeping only — the "delete class only
-- when empty" rule is enforced at the application layer, not the DB).
-- schema.sql is updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.classes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.class_teachers (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  teacher_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, teacher_id)
);
-- PK prefix (class_id) serves "members of a class"; this index serves the
-- reverse lookup "classes a teacher teaches".
CREATE INDEX IF NOT EXISTS class_teachers_teacher_idx
  ON public.class_teachers (teacher_id);

CREATE TABLE IF NOT EXISTS public.class_students (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, student_id),
  UNIQUE (student_id)        -- ép 1 lớp / học sinh (v1)
);
CREATE INDEX IF NOT EXISTS class_students_class_idx
  ON public.class_students (class_id);

-- Defense-in-depth RLS (service-role connection bypasses it; blocks bare
-- anon/authenticated keys if ever leaked). Idempotent.
ALTER TABLE public.classes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_students ENABLE ROW LEVEL SECURITY;
