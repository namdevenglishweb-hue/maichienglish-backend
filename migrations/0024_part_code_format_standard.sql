-- ============================================================
-- Migration 0024 — part presets persistence.
--
-- sections.part_code: nullable; NULL = custom section (builder tự do như cũ),
--   else a Cambridge Part preset id (e.g. 'KET_R_P3'). The allowed set lives in
--   code (services/presets.py), validated at the service layer — NO CHECK enum
--   here so adding a Part is a code change, not a migration.
-- exams.format_standard: nullable; 'cambridge_2020' marks an exam scaffolded to
--   the Cambridge standard. NULL = free-form (as today).
--
-- Both nullable + additive ⇒ fully backward-compatible (existing rows = NULL).
-- The AI-generation flow is UNCHANGED: it still takes part_code from the request
-- (optional); this column only lets the builder PERSIST a section's preset.
--
-- See docs/exam-part-presets/. Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

ALTER TABLE public.sections   ADD COLUMN IF NOT EXISTS part_code       text;
ALTER TABLE public.exams      ADD COLUMN IF NOT EXISTS format_standard text;

-- Lookups by part_code over ACTIVE sections (audit / builder filters).
CREATE INDEX IF NOT EXISTS idx_sections_part_code
    ON public.sections (part_code)
    WHERE deleted_at IS NULL AND part_code IS NOT NULL;
