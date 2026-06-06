-- ============================================================
-- Migration 0015 — Multi-class students (class-management v2).
-- Source of truth: docs/class-management/class-management-design.md §10.1
--
-- Drops the UNIQUE(student_id) constraint on class_students so a student
-- can belong to MULTIPLE classes at once (client changed the rule from
-- "1 class per student" to "many classes per student").
--
-- The PRIMARY KEY (class_id, student_id) STAYS → still blocks adding the
-- same student to the SAME class twice.
--
-- Additive/idempotent (DROP CONSTRAINT IF EXISTS). schema.sql updated lockstep.
-- ============================================================

ALTER TABLE public.class_students
  DROP CONSTRAINT IF EXISTS class_students_student_id_key;
