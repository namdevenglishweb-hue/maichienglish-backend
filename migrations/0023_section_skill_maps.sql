-- ============================================================
-- Migration 0023 — section_skill_maps (exam-gen v3 spec mode).
--
-- Caches the ANALYZE output (abstract "skill map") per section so re-running
-- generation on the same source doesn't pay the ANALYZE call again.
-- source_hash = sha256 of the exact payload ANALYZE saw (canonical JSON,
-- after position normalization) — lookup is hash-keyed, so editing the
-- section lazily invalidates the cache (no triggers). `model` is audit-only,
-- NOT part of the lookup key (design decision #16). Concurrent jobs may both
-- analyze + upsert: last write wins, acceptable (wasted call, no bad data).
--
-- See docs/exam-gen-v3-spec-mode/exam-gen-v3-spec-mode-design.md §6.2.
-- Additive/idempotent. schema.sql updated lockstep.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.section_skill_maps (
    section_id  uuid PRIMARY KEY REFERENCES public.sections(id) ON DELETE CASCADE,
    skill_map   jsonb NOT NULL,
    source_hash text NOT NULL,
    model       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.section_skill_maps ENABLE ROW LEVEL SECURITY;
