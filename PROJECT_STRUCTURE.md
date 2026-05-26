# Mai Chi English Backend — Project Structure

> **Purpose**: Map of every file in this repo with a one-line note on its role.
> **Maintenance rule**: Whenever a file is added, renamed, deleted, or has its responsibility changed, update this file in the same commit.
>
> Status legend: `✅` exists · `⏳` planned (not yet created)

---

## Repository Root

```
maichienglish-be/
├── main.py                          # ✅ FastAPI app entry — lifespan inits DB pool, uses setup_logging(), mounts auth/users/subscriptions/admin/exams/sections/questions/attempts/parents routers, exposes /health and /db-ping
├── requirements.txt                 # ✅ Pinned deps: fastapi, uvicorn, pydantic, pydantic-settings, email-validator, asyncpg, pyjwt, bcrypt
├── Dockerfile                       # ✅ Python 3.14-slim, non-root appuser, EXPOSE 8000, HEALTHCHECK on /health
├── render.yaml                      # ✅ Render web service: docker runtime, Singapore region, free plan, autoDeploy:false (deploy triggered by GHA after CI passes), healthCheckPath /health
├── schema.sql                       # ✅ Initial Postgres schema (Exam → Section → Question, attempt_section_state). Paste into Supabase SQL Editor on first setup. Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3
├── .env.example                     # ✅ Template for DATABASE_URL, DEBUG, CORS_ORIGINS, CORS_ORIGIN_REGEX, JWT_*, ADMIN_* (seed-only)
├── .env                             # ⏳ Local secrets (gitignored)
├── .gitignore                       # ✅ Ignore .env, __pycache__, .venv, .pytest_cache, IDE files
├── README.md                        # ✅ Project intro + quickstart
├── PROJECT_STRUCTURE.md             # ✅ This file — repo map
├── DEPLOYMENT.md                    # ✅ Step-by-step: GitHub setup, Supabase setup, Render setup, ongoing deploys, migrations, rollback, troubleshooting
├── FRONTEND_EXAM_GUIDE.md           # ✅ FE-facing integration spec for exam subsystem (auth recap, data model, full endpoint catalog, question types + studentAnswer shapes, {{gap:N}} rendering, shared-options detection, audio cap, RBAC, TS types, FAQ)
└── MAICHIENGLISH_BACKEND_PLAN.md    # ✅ Backend spec + HLD (source of truth for schema, API, decisions)
```

## Configuration

```
config/
├── __init__.py                      # ✅ Empty package marker
├── settings.py                      # ✅ Pydantic `Settings(BaseSettings)` — app_name, port, DATABASE_URL, DEBUG, CORS_ORIGINS, CORS_ORIGIN_REGEX, JWT_*, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; cached via @lru_cache; warns when JWT secret is still default
├── database.py                      # ✅ asyncpg pool lifecycle — init_db_pool / close_db_pool / get_db_pool
└── logging.py                       # ✅ `setup_logging()` — stdout handler, common format, quiets httpx/uvicorn.access
```

## API Layer (HTTP routes + request/response schemas)

```
api/
├── __init__.py                      # ✅ Empty package marker (main.py imports each subpackage's router directly, no aggregator)
├── common.py                        # ✅ Cross-domain shared request schemas: BatchDeleteRequest + MAX_BATCH_SIZE (=100). Kept outside per-domain packages to avoid circular imports (sections + questions both need it).
│
├── auth/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ POST /login, /refresh, /verify, /password/request-code, /password/reset
│   └── schemas.py                   # ✅ LoginRequest, LoginResponse, RefreshRequest/Response, VerifyResponse, PasswordResetCodeRequest/Response (devCode field), PasswordResetRequest/Response
│
├── users/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ GET /api/users/me + PUT /api/users/me (self-edit fullName/phone)
│   └── schemas.py                   # ✅ UserMeResponse, UserMeView, UserProfileUpdate, UserSubscriptionFull
│
├── admin/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ GET /users (paginated list, role filter), POST /users, DELETE /users/{id}, POST /users/{id}/reset-password, PUT /users/{student_id}/parent, PUT /subscriptions/{user_id} (all require_admin)
│   └── schemas.py                   # ✅ AdminCreateUserRequest (includes parentId), AdminResetPasswordRequest, AdminLinkParentRequest, AdminUpdateSubscriptionRequest, AdminUserListResponse, AdminUserSubscriptionView (nested in AdminUserView), PaginationView + response wrappers
│
├── exams/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ GET / (filter level/skill/published), GET /{id} (?include=sections nests section tree), POST / (nested mode: optional sections[] + questions[] in 1 txn with gap-marker validation), PUT /{id}, POST /{id}/publish (needs ≥1 section w/ ≥1 active question), POST /{id}/unpublish, DELETE /{id} (soft), DELETE /{id}/hard. Students forced to is_published=true.
│   └── schemas.py                   # ✅ ExamCreate (optional nested `sections[]`), ExamUpdate, ExamView (optional `sections` populated by ?include=sections), ExamSectionPreview (carries `type`), ExamQuestionPreview, ExamResponseData (carries optional `createdCounts`), ExamResponse, ExamListResponse
│
├── sections/                        # ✅ Middle layer of Exam → Section → Question
│   ├── __init__.py                  # ✅ Re-exports both routers
│   ├── routes.py                    # ✅ exam_scoped_router: GET/POST /api/exams/{eid}/sections (POST nested: optional questions[] in 1 txn)  |  section_router: PUT /api/sections/batch + POST /api/sections/batch-delete[?hard=true] (registered FIRST), then GET/PUT/DELETE /api/sections/{sid}, DELETE /api/sections/{sid}/hard (?include=questions on GET)
│   └── schemas.py                   # ✅ SectionCreate (optional nested `questions[]`, `type` field), SectionUpdate, SectionMaterial = Union[TextMaterial, ImageMaterial, AudioMaterial] (discriminated on `type`; audio carries `url` + section-wide `max_audio_plays` cap), SectionView (carries `type`; optional `questions` populated by ?include=questions), SectionQuestionPreview, SectionResponseData (optional `createdCounts`), SectionBatchUpdateItem/Request/Response + wrappers
│
├── questions/
│   ├── __init__.py                  # ✅ Re-exports both routers
│   ├── routes.py                    # ✅ section_scoped_router: GET/POST /api/sections/{sid}/questions  |  question_router: PUT /api/questions/batch + POST /api/questions/batch-delete[?hard=true] (registered FIRST), then GET/PUT/DELETE /api/questions/{id}, DELETE /api/questions/{id}/hard. Excel import deferred to B3.4b.
│   └── schemas.py                   # ✅ QuestionCreate (server-side per-type validation; MC + matching share {stem, options, correct_index} shape; MC options accept text|image_url), QuestionUpdate, QuestionView, QuestionBatchUpdateItem/Request/Response + wrappers
│
├── attempts/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ POST / (start, enforces tier limit, returns nested exam→sections→questions; sections carry typed materials incl. audio), POST /{id}/submit (auto-grade across all sections), POST /{id}/sections/{sid}/audio-play?materialIndex=N (per-audio counter, shared cap), GET /history, GET /{id} (detail grouped by section; owner/staff/parent)
│   └── schemas.py                   # ✅ AttemptStart/Submit Request+Response (nested sections), AttemptSectionView (carries `type`; no audioUrl — audio in materials), AttemptDetailResponse, AttemptHistoryItem, AudioPlayResponse (materialIndex + per-audio audioPlayCount + maxPlays)
│
├── parents/
│   ├── __init__.py                  # ✅ Re-exports `router` (router-level require_parent)
│   ├── routes.py                    # ✅ GET /api/parents/me/children, GET .../children/{student_id}/attempts, GET .../{attempt_id} — all gated to parent's linked children
│   └── schemas.py                   # ✅ ChildView, ChildrenListResponse (attempt schemas reused from api/attempts)
│
└── subscriptions/
    ├── __init__.py                  # ✅ Re-exports `router`
    ├── routes.py                    # ✅ GET /api/subscriptions/me, GET /api/subscriptions/plans
    └── schemas.py                   # ✅ SubscriptionView, PlansResponse, PlanView, PlanFeatureView
```

## Service Layer (business logic — no HTTP imports)

```
services/
├── __init__.py                      # ✅ Empty
├── exceptions.py                    # ✅ ServiceError base + NotFoundError, AlreadyExistsError, ValidationError, PermissionDeniedError, InvalidCredentialsError, InsufficientCreditsError
├── auth_service.py                  # ✅ Password reset code lifecycle: request_password_reset_code (anti-enumeration silent 200 + invalidate previous codes), reset_password (bcrypt-compare candidates, mark used, replace password_hash in one tx). Login/token logic stays in api/auth/routes.py + utils/jwt_utils.py.
├── user_service.py                  # ✅ create_user (profile + subscription tx, accepts parent_id), authenticate, get_by_email/id, list_users (filter+paginate), update_profile (self-edit fullName/phone), delete_user, admin_reset_password, link_parent, list_children_of_parent, is_child_of
├── exam_service.py                  # ✅ Exam CRUD (no passage/audio fields), publish (checks ≥1 section w/ ≥1 active question) / unpublish, soft delete, hard delete (CASCADE through sections). `create_exam_nested` builds whole exam+sections+questions tree in 1 transaction with gap-marker validation.
├── section_service.py               # ✅ Section CRUD with `type` hint + bulk methods. _validate_materials accepts 3 typed variants (text/image/audio) via discriminated union. create_section, create_section_with_questions (nested inline questions in 1 txn), list_sections_by_exam, get_section, update_section, soft/hard delete, bulk_update_sections, bulk_delete_sections. Module-level `validate_gap_markers` reused by both section_service and exam_service.
├── question_service.py              # ✅ Question CRUD scoped to section_id (per-section position); per-type validation (matching reuses MC validator). Single + bulk_update_questions + bulk_delete_questions. Excel import lands in B3.4b.
├── attempt_service.py               # ✅ Start (enforces tier limit; returns nested exam→sections→questions), submit + auto-grade across all sections, history queries, record_audio_play (per-section: upserts attempt_section_state, enforces sections.max_audio_plays). Custom AttemptLimitExceededError + AudioPlayLimitExceededError extend PermissionDeniedError.
├── subscription_service.py          # ✅ get_by_user_id, update_tier (validates tier + logs), list_plans serializer. Attempt-limit enforcement lives in attempt_service; period-reset is not yet implemented (period boundary still equals subscriptions.current_period_start from creation).
└── subscription_plans.py            # ✅ PlanTier enum, SubscriptionPlan + PlanFeature dataclasses, SUBSCRIPTION_PLANS dict (Free / Basic / Pro / Ultra)
```

## Data Models (SQLAlchemy ORM — optional)

```
models/
├── __init__.py                      # ⏳ Empty
├── user.py                          # ⏳ Profile model (matches §3.1 profiles table)
├── subscription.py                  # ⏳ Subscription model (matches §3.2)
├── exam.py                          # ⏳ Exam model with deleted_at (matches §3.4)
├── section.py                       # ⏳ Section model with materials JSONB (typed blocks) + max_audio_plays + deleted_at (matches §3.5)
├── question.py                      # ⏳ Question model with question_data JSONB + section_id + deleted_at (matches §3.6)
├── attempt.py                       # ⏳ Attempt model (matches §3.7)
├── attempt_section_state.py         # ⏳ Per-section progress + per-audio counter map (matches §3.8)
└── answer.py                        # ⏳ Answer model (matches §3.9)
```

> Note: if the implementation uses raw asyncpg with dict rows instead of SQLAlchemy ORM, this folder may stay empty or be removed. Decide at implementation time.

## Utilities (generic helpers)

```
utils/
├── __init__.py                      # ✅ Empty
├── jwt_utils.py                     # ✅ TokenType constants, create_access_token, create_refresh_token, decode_token (with type verification)
├── password_utils.py                # ✅ hash_password, verify_password (bcrypt cost 12)
├── grading_utils.py                 # ✅ grade_question (multiple_choice index match against options with text|image_url variants / fill_blank string-match w/ case_sensitive / matching set-of-pairs compare), strip_correct (removes answer fields before serving to students mid-attempt)
└── excel_utils.py                   # ⏳ Excel-to-questions parser (B3.4b — deferred, awaiting client confirmation on column format)
```

## Top-level Dependencies (FastAPI DI)

```
dependencies.py                      # ✅ get_current_user (Bearer JWT validator), require_admin, require_teacher_or_admin, require_parent, require_subscription_tier (factory)
```

## Database Migrations

> [`schema.sql`](schema.sql) is the **current-state snapshot** used by `scripts/init_schema.py --drop` for fresh installs (loses data).
> [`migrations/`](migrations/) holds **incremental SQL** for existing databases that need upgrading without losing data. Run each one manually in the Supabase SQL Editor in numeric order. When the count grows, swap in a real migration tool (Alembic).

```
migrations/
├── 0002_add_parent_role.sql         # ✅ Add `parent` to role CHECK + `profiles.parent_id` self-FK. Idempotent.
├── 0003_add_password_reset_codes.sql # ✅ Create `password_reset_codes` table (bcrypt-hashed 6-digit codes, 10-min TTL). Idempotent.
├── 0004_exam_sections.sql           # ✅ Introduce sections layer + attempt_section_state. Drops exams.audio_url/passage/max_audio_plays + questions.exam_id + attempts.audio_play_count. Breaking change — dev DB only; run init_schema.py --drop -y for fresh setup.
├── 0005_section_type.sql            # ✅ Add `sections.type` (rendering hint: multiple_choice/fill_blank/matching, nullable). No data migration needed for matching shape change (prior shape never shipped). Idempotent.
├── 0006_materials_typed_blocks.sql  # ✅ Drop `sections.audio_url` (audio moves into `materials` JSONB as typed block). Replace `attempt_section_state.audio_play_count` (scalar) with `audio_play_counts jsonb` keyed by material_index for per-audio counters. Idempotent.
└── 0007_partial_unique_positions.sql # ✅ Hotfix: replace plain UNIQUE(exam_id, position) / UNIQUE(section_id, position) with PARTIAL unique indexes filtered by `deleted_at IS NULL`. Soft-deleted rows no longer block position reuse (was causing 500 UniqueViolation on POST /sections after admin soft-deletes any position). Idempotent.
```

```
scripts/
├── __init__.py                      # ✅ Empty package marker
├── init_schema.py                   # ✅ Apply schema.sql via asyncpg. Flags: --check, --drop, -y. Used for fresh setup + dev resets.
└── seed_admin.py                    # ✅ One-shot: create the first admin user from ADMIN_EMAIL/ADMIN_PASSWORD/ADMIN_FULL_NAME env vars. Idempotent (no-op if already exists).
```

## Tests

```
tests/
├── __init__.py                      # ⏳ Empty
├── conftest.py                      # ⏳ Pytest fixtures: test DB pool, FastAPI TestClient, sample users/exams
├── test_auth.py                     # ⏳ Login, refresh, verify, password reset flow
├── test_users.py                    # ⏳ GET /me, admin user CRUD
├── test_exams.py                    # ⏳ CRUD, publish gate, soft vs hard delete
├── test_questions.py                # ⏳ Per-type CRUD, Excel import
├── test_attempts.py                 # ⏳ Start, submit, grading correctness per question type, tier limit enforcement
└── test_subscriptions.py            # ⏳ Plan retrieval, admin tier update
```

## CI/CD

```
.github/
└── workflows/
    └── backend.yml                  # ✅ smoke (install + import) → deploy (Render API call, needs RENDER_API_KEY + RENDER_SERVICE_ID secrets). Lint (ruff + mypy) + pytest jobs are deferred to B3.6b.
```

---

## File-creation Order Suggestion

When starting implementation, build in this order so each layer has its dependencies ready:

1. **Foundation**: `requirements.txt`, `Dockerfile`, `.env.example`, `.gitignore`
2. **Config**: `config/settings.py`, `config/database.py`, `config/logging.py`
3. **Utils**: `utils/jwt_utils.py`, `utils/password_utils.py`
4. **Dependencies**: `dependencies.py`
5. **Auth slice end-to-end**: `services/user_service.py` → `api/auth/schemas.py` → `api/auth/routes.py` → `main.py` (mount auth router) → `tests/test_auth.py`
6. Repeat slice-by-slice for users, admin, exams, questions, attempts, subscriptions.
7. **CI/CD**: `.github/workflows/backend.yml`, `render.yaml`
