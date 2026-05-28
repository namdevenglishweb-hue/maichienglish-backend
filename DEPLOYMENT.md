# Mai Chi English Backend — Deployment Guide

> **Audience**: backend dev (or whoever holds the keys) bringing the API from local commit to production traffic.
> **Stack**: GitHub Actions CI → Render (Docker, Singapore) → Supabase (PostgreSQL 17 + Storage).

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Initial setup (one-time)](#3-initial-setup-one-time)
   - 3.1 Supabase project
   - 3.2 GitHub repo + secrets
   - 3.3 Render service (Blueprint from `render.yaml`)
   - 3.4 First successful deploy
4. [Ongoing deploys (every change)](#4-ongoing-deploys-every-change)
5. [Schema migrations](#5-schema-migrations)
6. [Rolling back](#6-rolling-back)
7. [Optional — split dev / staging / prod](#7-optional--split-dev--staging--prod)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Architecture overview

```
        Developer                        GitHub                       Render                  Supabase
            │                              │                            │                        │
            │  git push origin main        │                            │                        │
            ├─────────────────────────────►│                            │                        │
            │                              │  CI workflow               │                        │
            │                              │  (lint + smoke + test)     │                        │
            │                              │                            │                        │
            │                              │  if pass → curl Render API │                        │
            │                              ├───────────────────────────►│                        │
            │                              │                            │  pull main             │
            │                              │                            │  build Docker image    │
            │                              │                            │  start container       │
            │                              │                            │                        │
            │                              │                            │   asyncpg pool ───────►│
            │                              │                            │   (Postgres pooler)    │
            │                              │                            │                        │
            │                              │                            │   /health (probe) ←───┐│
            │                              │                            │                       ││
            │  https://maichienglish-api.onrender.com/health            │                       ││
            │ ◄────────────────────────────────────────────────────────────────────────────────┘│
```

Single source of truth for the schema lives in [`schema.sql`](schema.sql); migrations go under [`migrations/`](migrations/).

---

## 2. Prerequisites

You only need accounts (free tiers all work) + a local toolchain:

- GitHub account with push rights to the repo
- Supabase account (Pro plan optional — Free tier works for the dev box)
- Render account (Free tier works; first build is ~5 min, subsequent ~2 min)
- Local machine with:
  - Python 3.14 (or 3.12+) + virtual env
  - Git
  - `psql` or Supabase SQL Editor access for migrations

Optional but handy:
- `gh` CLI for managing secrets from terminal

---

## 3. Initial setup (one-time)

Follow these in order. After step 3.4 you have a working production URL.

### 3.1 Supabase project

1. Go to <https://supabase.com> → **New project**.
2. Settings:
   - **Region**: Singapore (`ap-southeast-1`) — best latency for Vietnam users
   - **Postgres version**: 17 (default for new projects)
   - **DB password**: generate a strong one and store it in a password manager
3. Once provisioned (~2 min), grab the following from **Project Settings**:
   - **Database → Connection string → "Session pooler"** (port 5432) → save as `DATABASE_URL`
     - Do **not** use the Transaction pooler (port 6543) — asyncpg's extended query protocol is incompatible.
     - Username will be `postgres.<project-ref>` (with the dot). Don't strip it.
   - **API → Project URL** → save as `SUPABASE_URL`
   - **API → `service_role` key** → save as `SUPABASE_SERVICE_ROLE_KEY`
4. Apply the schema (one of two options):
   - **Option A (recommended for first-time)**: open **SQL Editor** → paste the contents of [`schema.sql`](schema.sql) → Run.
     - When prompted, click **"Run and enable RLS"** for each `CREATE TABLE`. We don't define RLS policies because the backend connects with the service-role connection (which bypasses RLS); enabling RLS protects anon/authenticated key holders from ever reading these tables directly.
   - **Option B (from a dev machine that already has `DATABASE_URL`)**:
     ```powershell
     python scripts/init_schema.py
     ```
     This runs the same SQL via asyncpg. Add `--drop` to wipe existing tables first (dev only — destructive).
5. Create Storage buckets — **Storage → New bucket** twice. Set everything in the dialog (UI lets you do it in one step; see [MEDIA_UPLOAD.md §9.2](MEDIA_UPLOAD.md#92-setup-one-time-supabase-dashboard) for the full table):
   - `audio` — Public ✅, Restrict file uploads ✅, MIME `audio/mpeg, audio/mp4, audio/x-m4a, audio/m4a, audio/wav, audio/webm`, size limit `50 MB`
   - `images` — Public ✅, Restrict file uploads ✅, MIME `image/png, image/jpeg, image/webp`, size limit `10 MB`
6. Apply the storage RLS policy so anon/authenticated can read public bucket files. **SQL Editor** → paste [`migrations/0008_storage_rls_policy.sql`](migrations/0008_storage_rls_policy.sql) → Run. Without this, the FE HEAD verify in the upload flow returns 400/404.
7. **Don't forget** to also enable RLS on all 7 application tables manually after step 4 if you didn't tick "Run and enable RLS" — see [Troubleshooting](#8-troubleshooting).

### 3.2 GitHub repo + secrets

1. If the repo doesn't exist yet:
   ```powershell
   gh repo create maichienglish-backend --private
   ```
2. From the local clone, point `origin` at it and push `main`:
   ```powershell
   git remote add origin https://github.com/<org>/maichienglish-backend.git
   git push -u origin main
   ```
3. Add **repository secrets** at <https://github.com/`<org>/<repo>`/settings/secrets/actions> → **New repository secret**:

   | Secret | Value | Where it's used |
   |--------|-------|-----------------|
   | `RENDER_API_KEY` | Render → Account Settings → API Keys → Create | GHA `deploy` job |
   | `RENDER_SERVICE_ID` | From the Render service URL — the `srv-XXXXXXXX` segment | GHA `deploy` job |

   The two Render secrets won't exist until step 3.3 finishes — come back here after Render is set up.

### 3.3 Render service (Blueprint from `render.yaml`)

1. Render Dashboard → **New +** → **Blueprint**.
2. Connect the GitHub repo. Render reads [`render.yaml`](render.yaml) and proposes:
   - Service name: `maichienglish-api`
   - Runtime: Docker
   - Region: Singapore
   - Plan: Free
   - `autoDeploy: false` (we trigger from GHA after CI passes)
3. **Apply** the Blueprint. Render creates the service but **the first build will fail** — environment variables aren't set yet. That's expected.
4. Open the service → **Environment** tab → add these (each line marked `sync: false` in `render.yaml` must be set manually):

   | Variable | Required? | Source |
   |----------|-----------|--------|
   | `DATABASE_URL` | required | Supabase Session pooler (from step 3.1) |
   | `JWT_SECRET_KEY` | required | Generate locally: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `SUPABASE_URL` | required | Supabase Dashboard → Project Settings → API (used by media upload — `POST /api/admin/upload`) |
   | `SUPABASE_SERVICE_ROLE_KEY` | required | Same page (do NOT use the anon key) |
   | `STORAGE_PROVIDER` | optional | Default `supabase`. Change only when an S3 adapter ships. |
   | `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` | optional | Override only if defaults don't match your FE origin |

5. **Save Changes** → Render kicks off a fresh build. Watch the **Logs** tab.
6. Once status reads **Live**, copy the public URL (e.g. `https://maichienglish-api.onrender.com`) → put it in your password manager.
7. Now grab the **service ID** from the URL bar — `https://dashboard.render.com/web/srv-XXXXXXXX` → the `srv-XXXXXXXX` part is `RENDER_SERVICE_ID`. Set it back on GitHub (step 3.2).

### 3.4 First successful deploy

After all secrets are in place:

```powershell
# Trigger a no-op deploy by amending or making a trivial commit + push.
# Or: GitHub Actions tab → backend.yml → Re-run latest run.
git commit --allow-empty -m "ci: trigger deploy"
git push origin main
```

Watch:
- **GitHub Actions** (`/actions`) — `smoke` job passes → `deploy` job calls Render API.
- **Render Logs** — pull → build → start uvicorn → `/health` returns 200.

Smoke-test the live URL:
```powershell
curl https://maichienglish-api.onrender.com/health
# → {"status":"ok","service":"maichienglish-api"}

curl https://maichienglish-api.onrender.com/db-ping
# → {"status":"ok","result":1}
```

If both return 200 — you're done. Seed the first admin:

```powershell
# locally, with .env pointing at the same Supabase
python scripts/seed_admin.py
```

---

## 4. Ongoing deploys (every change)

For every code change:

```powershell
# 1. Branch + commit
git checkout -b feat/<short-name>
# ...edit files...
git add -A
git commit -m "feat: <what changed and why>"

# 2. Push branch + open PR
git push -u origin feat/<short-name>
gh pr create --base main --fill

# 3. CI runs on the PR. Review + merge to main.
gh pr merge --squash --delete-branch
```

When `main` advances:
1. GitHub Actions `smoke` job runs.
2. If green → `deploy` job calls Render's deploy API.
3. Render pulls the new commit, rebuilds the Docker image, restarts the container.
4. `/health` probe must return 200 before traffic shifts. Old container keeps serving until then — **zero-downtime by default**.

**Total time from `git push` to live**: ~3-5 minutes.

To monitor a specific deploy:
- Render service → **Events** tab shows each deploy with status + duration.
- Render service → **Logs** for runtime output (uvicorn access log is quieted; only WARNING+ shows).

---

## 5. Schema migrations

The schema is split between two artifacts:

| File | When to use |
|------|-------------|
| [`schema.sql`](schema.sql) | Fresh installs only. Re-running on an existing DB will fail. |
| [`migrations/000X_*.sql`](migrations/) | Incremental changes applied in numeric order. Idempotent (`IF NOT EXISTS` / dynamic `DROP CONSTRAINT`). |

### Applying a new migration

When a PR includes a `migrations/000X_*.sql` file:

1. **Before merging the PR**, run the migration against the live Supabase project:
   - Supabase Dashboard → **SQL Editor** → paste the migration file → **Run**.
   - Or locally: `python scripts/init_schema.py` won't auto-pick migrations; copy-paste is the current workflow.
2. Verify the change (e.g. `\d+ <table>` in psql, or Supabase Table Editor).
3. Now merge the PR. The application code expects the new schema and will start using it on the next deploy.

> **Migration ordering rule**: schema first, then code. If you merge code that expects new columns before applying the migration, Render will start failing at the SQL layer.

### Authoring a new migration

1. Pick the next number: `migrations/0004_<short-name>.sql` (we already have 0002, 0003).
2. Write idempotent DDL — use `IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, dynamic constraint drops.
3. Also update [`schema.sql`](schema.sql) so fresh installs match — both must converge to the same end state.
4. Update plan §3 if the schema is meaningfully changed.

---

## 6. Rolling back

### Code rollback (one bad commit)

Render keeps the last several builds. To roll back without a code change:

1. Render service → **Events** tab → find a previous successful deploy.
2. Click the **Rollback** action on that event.
3. Render restarts the previous image — ~30 seconds.

For a clean fix forward:
```powershell
git revert <bad-commit-sha>
git push origin main
```
The revert commit goes through the normal CI → deploy path.

### Schema rollback (much harder)

There is no automatic schema rollback. If a migration broke production:

1. Write a **compensating migration** (e.g. `0005_revert_field_X.sql`) that undoes the change.
2. Apply it via Supabase SQL Editor.
3. Then code-rollback as above.

> Schema migrations should be **additive whenever possible** (new columns nullable, new tables) so you never need to reverse them under fire.

---

## 7. Optional — split dev / staging / prod

The current setup is a single environment ("main = prod"). If you want a separate dev:

1. **Second Supabase project** (Free tier — same Singapore region) → save its `DATABASE_URL` separately.
2. **Second Render service** — clone `render.yaml` to `render.dev.yaml` (or create a Blueprint instance per branch). Point its `DATABASE_URL` at the dev Supabase.
3. **GitHub branch**: `develop` lives alongside `main`. Modify `.github/workflows/backend.yml` so:
   - Push to `develop` → deploy to the dev Render service (different `RENDER_SERVICE_ID` secret).
   - Push to `main` → deploy to prod Render service (existing secret).
4. **Branch protection**: enable on `main` → require PR + passing CI + 1 review.

This is documented as planned but **not yet implemented** — call when you want it set up.

---

## 8. Troubleshooting

### `/db-ping` returns 503 "Database unreachable: getaddrinfo failed"

DNS for the configured `DATABASE_URL` doesn't resolve. Two common causes:
- The host has a `[PROJECT]` placeholder still in it — replace with the real project ref.
- You used the Direct connection (`db.<ref>.supabase.co`) which is IPv6-only and most Vietnam ISPs don't route. Switch to **Session pooler** (`aws-0-<region>.pooler.supabase.com`).

### CORS blocked from frontend even after deploy

- Browser caches preflight for 10 minutes. Try Incognito or DevTools → Network → Disable cache.
- Run `curl -I -X OPTIONS https://maichienglish-api.onrender.com/health -H "Origin: <frontend-origin>"` — backend should echo `access-control-allow-origin`. If it doesn't, check `CORS_ORIGINS` / `CORS_ORIGIN_REGEX` env vars on Render.

### Login returns 401 "Email hoặc mật khẩu không đúng"

- The seeded admin password may not match what you're typing. Run `scripts/seed_admin.py` again — it's a no-op if the user exists, but if you wiped the DB you need to re-seed.
- Check the JWT secret on Render isn't still the literal `change-me-in-prod` — the app warns at startup if so.

### Supabase popup "This query creates a table without enabling Row Level Security"

Always pick **"Run and enable RLS"**. Service-role connection bypasses RLS anyway, and bare anon/authenticated keys (if ever exposed) are blocked from reading these tables. Verify after:
```sql
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
-- rowsecurity should be `true` for every row.
```

If any table is missing RLS:
```sql
ALTER TABLE public.<tablename> ENABLE ROW LEVEL SECURITY;
```

### Render deploy fails mid-build

- Open the failed deploy's **Logs**. Most common failure: missing env var (look for `pydantic.ValidationError: DATABASE_URL`).
- Set the variable → Render auto-redeploys.

### GHA `deploy` job fails with 401

`RENDER_API_KEY` is missing or expired. Rotate at Render → Account Settings → API Keys.

---

## Quick reference — current production

| Resource | URL / Path |
|----------|------------|
| Public API | `https://maichienglish-api.onrender.com` |
| API docs (Swagger) | `https://maichienglish-api.onrender.com/docs` |
| Health probe | `/health` |
| DB connectivity probe | `/db-ping` |
| GitHub repo | `https://github.com/namdevenglishweb-hue/maichienglish-backend` |
| Render service ID | `srv-...` (set in repo secrets) |
| Supabase project | (link from Supabase dashboard) |
