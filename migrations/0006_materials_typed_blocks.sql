-- ============================================================
-- Migration 0006 — materials become typed blocks (text/image/audio).
-- Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3.5 / §3.8.
--
-- Schema changes:
--   - DROP sections.audio_url           — audio now lives inside materials JSONB
--   - GIỮ sections.max_audio_plays      — section-wide cap value (unchanged)
--   - DROP attempt_section_state.audio_play_count    (scalar)
--   - ADD attempt_section_state.audio_play_counts    (jsonb map)
--
-- Counter semantics:
--   - audio_play_counts = {"<material_index>": <play_count>, ...}
--   - Each audio material in a section has its own independent counter.
--   - All counters check against the SAME section.max_audio_plays cap.
--   - Caveat (documented in FE guide): material_index is positional —
--     admin reordering materials mid-attempt can misalign counters.
--
-- No data migration needed — sections.audio_url is unset on the live
-- DB (we just shipped 0005; no production exam content yet).
-- Idempotent.
-- ============================================================

ALTER TABLE public.sections             DROP COLUMN IF EXISTS audio_url;
ALTER TABLE public.attempt_section_state DROP COLUMN IF EXISTS audio_play_count;
ALTER TABLE public.attempt_section_state
  ADD COLUMN IF NOT EXISTS audio_play_counts jsonb NOT NULL DEFAULT '{}'::jsonb;
