-- ============================================================
-- Migration 0008 — storage RLS policy for media buckets
--
-- Supabase enables RLS on `storage.objects` by default. Even a
-- "public" bucket needs an explicit SELECT policy on `storage.objects`
-- for anon/authenticated roles to read file metadata — without it,
-- the `/storage/v1/object/public/<bucket>/<path>` URL returns 400/404
-- and the FE HEAD verify (step 7 of the upload flow) fails.
--
-- This policy allows ANY caller (anon included) to SELECT rows of
-- `storage.objects` belonging to the `audio` or `images` bucket.
-- Upload remains restricted to `service_role` (used by BE only) —
-- we do NOT add an INSERT/UPDATE/DELETE policy here.
--
-- Idempotent: DROP IF EXISTS before CREATE.
-- ============================================================

DROP POLICY IF EXISTS "Public read access for exam media" ON storage.objects;

CREATE POLICY "Public read access for exam media"
    ON storage.objects FOR SELECT
    USING (bucket_id IN ('audio', 'images'));
