# Mai Chi English Backend — Project Structure

> **Purpose**: Map of every file in this repo with a one-line note on its role.
> **Maintenance rule**: Whenever a file is added, renamed, deleted, or has its responsibility changed, update this file in the same commit.
>
> Status legend: `✅` exists · `⏳` planned (not yet created)

---

## Repository Root

```
maichienglish-be/
├── main.py                          # ✅ FastAPI app entry — lifespan inits DB pool, uses setup_logging(), mounts auth/users/subscriptions/admin/exams/questions/attempts/parents routers, exposes /health and /db-ping
├── requirements.txt                 # ✅ Pinned deps: fastapi, uvicorn, pydantic, pydantic-settings, email-validator, asyncpg, pyjwt, bcrypt
├── Dockerfile                       # ✅ Python 3.14-slim, non-root appuser, EXPOSE 8000, HEALTHCHECK on /health
├── render.yaml                      # ✅ Render web service: docker runtime, Singapore region, free plan, autoDeploy:false (deploy triggered by GHA after CI passes), healthCheckPath /health
├── schema.sql                       # ✅ Initial Postgres schema — paste into Supabase SQL Editor on first setup. Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3
├── .env.example                     # ✅ Template for DATABASE_URL, DEBUG, CORS_ORIGINS, CORS_ORIGIN_REGEX, JWT_*, ADMIN_* (seed-only)
├── .env                             # ⏳ Local secrets (gitignored)
├── .gitignore                       # ✅ Ignore .env, __pycache__, .venv, .pytest_cache, IDE files
├── README.md                        # ✅ Project intro + quickstart
├── PROJECT_STRUCTURE.md             # ✅ This file — repo map
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
│   ├── routes.py                    # ✅ GET / (filter level/skill/published), GET /{id}, POST /, PUT /{id}, POST /{id}/publish, POST /{id}/unpublish, DELETE /{id} (soft), DELETE /{id}/hard. Students forced to is_published=true.
│   └── schemas.py                   # ✅ ExamCreate, ExamUpdate, ExamView, ExamResponse, ExamListResponse
│
├── questions/
│   ├── __init__.py                  # ✅ Re-exports both routers
│   ├── routes.py                    # ✅ exam_scoped_router: GET/POST /api/exams/{id}/questions  |  question_router: GET/PUT/DELETE /api/questions/{id}, DELETE /api/questions/{id}/hard. Excel import deferred to B3.4b.
│   └── schemas.py                   # ✅ QuestionCreate (server-side per-type validation), QuestionUpdate, QuestionView + wrappers
│
├── attempts/
│   ├── __init__.py                  # ✅ Re-exports `router`
│   ├── routes.py                    # ✅ POST / (start, enforces tier limit), POST /{id}/submit (auto-grade), POST /{id}/audio-play (listening cap), GET /history, GET /{id} (detail; owner/staff/parent)
│   └── schemas.py                   # ✅ AttemptStart/Submit Request+Response, AttemptDetailResponse, AttemptHistoryItem, AudioPlayResponse
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
├── exam_service.py                  # ✅ Exam CRUD, publish (checks >=1 active question) / unpublish, soft delete (set deleted_at), hard delete (CASCADE)
├── question_service.py              # ✅ Question CRUD with Pydantic per-type validation of question_data (multiple_choice / fill_blank / matching), auto-assigned position, soft/hard delete. Excel import lands in B3.4b.
├── attempt_service.py               # ✅ Start (enforces tier limit via COUNT vs subscription.current_period_start), submit + auto-grade, history queries, record_audio_play (enforces exams.max_audio_plays). Custom AttemptLimitExceededError + AudioPlayLimitExceededError extend PermissionDeniedError.
├── subscription_service.py          # ✅ get_by_user_id, update_tier (validates tier + logs), list_plans serializer. Attempt-limit enforcement lives in attempt_service; period-reset is not yet implemented (period boundary still equals subscriptions.current_period_start from creation).
└── subscription_plans.py            # ✅ PlanTier enum, SubscriptionPlan + PlanFeature dataclasses, SUBSCRIPTION_PLANS dict (Free / Basic / Pro / Ultra)
```

## Data Models (SQLAlchemy ORM — optional)

```
models/
├── __init__.py                      # ⏳ Empty
├── user.py                          # ⏳ Profile model (matches §3.1 profiles table)
├── exam.py                          # ⏳ Exam model with deleted_at (matches §3.3)
├── question.py                      # ⏳ Question model with question_data JSONB + deleted_at (matches §3.4)
├── attempt.py                       # ⏳ Attempt model (matches §3.5)
├── answer.py                        # ⏳ Answer model (matches §3.6)
└── subscription.py                  # ⏳ Subscription model (matches §3.2)
```

> Note: if the implementation uses raw asyncpg with dict rows instead of SQLAlchemy ORM, this folder may stay empty or be removed. Decide at implementation time.

## Utilities (generic helpers)

```
utils/
├── __init__.py                      # ✅ Empty
├── jwt_utils.py                     # ✅ TokenType constants, create_access_token, create_refresh_token, decode_token (with type verification)
├── password_utils.py                # ✅ hash_password, verify_password (bcrypt cost 12)
├── grading_utils.py                 # ✅ grade_question (multiple_choice index match / fill_blank string-match w/ case_sensitive / matching set-of-pairs compare), strip_correct (removes answer fields before serving to students mid-attempt)
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
└── 0003_add_password_reset_codes.sql # ✅ Create `password_reset_codes` table (bcrypt-hashed 6-digit codes, 10-min TTL). Idempotent.
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
