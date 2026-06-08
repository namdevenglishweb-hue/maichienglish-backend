-- ============================================================
-- Migration 0019 — Admin per-type prompt config for AI generation.
-- Source of truth: docs/exam-ai-generation/exam-ai-generation-design.md §10
--
-- One row per section type holding an "additional prompt" the admin
-- uses to teach the AI business conventions for that type (source A).
-- Injected at generation time, below structure/quality invariants.
-- Per-section ad-hoc prompts (source B) are NOT stored here — they ride
-- in the generate request and are kept only in generation_meta.
--
-- type CHECK mirrors sections.type allowed values (incl form_completion,
-- added in 0014). Empty table is valid (no prompt injected).
--
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.section_type_prompts (
    type              text PRIMARY KEY
                        CHECK (type IN ('multiple_choice', 'multiple_choice_shared',
                                        'fill_blank', 'matching', 'writing', 'speaking',
                                        'form_completion')),
    additional_prompt text NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL
);

ALTER TABLE public.section_type_prompts ENABLE ROW LEVEL SECURITY;
