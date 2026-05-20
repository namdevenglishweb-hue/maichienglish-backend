# Mai Chi English Backend — Project Structure

> **Purpose**: Map of every file in this repo with a one-line note on its role.
> **Maintenance rule**: Whenever a file is added, renamed, deleted, or has its responsibility changed, update this file in the same commit.
>
> Status legend: `✅` exists · `⏳` planned (not yet created)

---

## Repository Root

```
maichienglish-be/
├── main.py                          # ✅ FastAPI app entry — lifespan inits DB pool, exposes /health and /db-ping (B1 walking skeleton)
├── requirements.txt                 # ✅ Pinned deps for B1: fastapi, uvicorn[standard], pydantic, pydantic-settings, asyncpg
├── Dockerfile                       # ✅ Python 3.14-slim, non-root appuser, EXPOSE 8000, HEALTHCHECK on /health
├── render.yaml                      # ✅ Render web service: docker runtime, Singapore region, free plan, autoDeploy:false (deploy triggered by GHA after CI passes), healthCheckPath /health
├── schema.sql                       # ✅ Initial Postgres schema — paste into Supabase SQL Editor on first setup. Source of truth: MAICHIENGLISH_BACKEND_PLAN.md §3
├── .env.example                     # ✅ Template for DATABASE_URL + DEBUG + CORS_ORIGINS (JWT vars added in B3.1)
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
├── settings.py                      # ✅ Pydantic `Settings(BaseSettings)` — DATABASE_URL + DEBUG (more keys added as needed), cached via @lru_cache
├── database.py                      # ✅ asyncpg pool lifecycle — init_db_pool / close_db_pool / get_db_pool
└── logging.py                       # ⏳ `setup_logging()` — configures stdout handler, format, quiets httpx/uvicorn noise
```

## API Layer (HTTP routes + request/response schemas)

```
api/
├── __init__.py                      # ⏳ Aggregates all routers — imports each domain router and re-exports them for main.py
│
├── auth/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ POST /login, /refresh, /verify, /password/request-code, /password/reset
│   └── schemas.py                   # ⏳ LoginRequest, LoginResponse, RefreshRequest, VerifyResponse, PasswordReset* schemas
│
├── users/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ GET /me — current user's profile + subscription
│   └── schemas.py                   # ⏳ UserMeResponse, UserSubscriptionView
│
├── admin/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ Admin-only: POST /users, DELETE /users/{id}, POST /users/{id}/reset-password, PUT /subscriptions/{user_id}
│   └── schemas.py                   # ⏳ AdminCreateUserRequest, AdminResetPasswordRequest, AdminUpdateSubscriptionRequest
│
├── exams/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ GET / (list with level/skill filters), POST /, PUT /{id}, POST /{id}/publish, DELETE /{id} (soft), DELETE /{id}/hard (admin only, CASCADE)
│   └── schemas.py                   # ⏳ ExamCreate, ExamUpdate, ExamResponse, ExamListResponse
│
├── questions/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ GET /exams/{id}/questions, POST /exams/{id}/questions, PUT /{id}, DELETE /{id} (soft), DELETE /{id}/hard, POST /exams/{id}/questions/import (Excel)
│   └── schemas.py                   # ⏳ QuestionCreate, QuestionUpdate, QuestionResponse, plus per-type discriminated unions for question_data
│
├── attempts/
│   ├── __init__.py                  # ⏳ Empty
│   ├── routes.py                    # ⏳ POST / (start), POST /{id}/submit, GET /{id} (detail), GET /history
│   └── schemas.py                   # ⏳ AttemptStartRequest/Response, AttemptSubmitRequest/Response, AttemptDetailResponse, AttemptHistoryItem
│
└── subscriptions/
    ├── __init__.py                  # ⏳ Empty
    ├── routes.py                    # ⏳ GET /me, GET /plans (public plan definitions)
    └── schemas.py                   # ⏳ SubscriptionResponse, PlanResponse, PlanFeatureView
```

## Service Layer (business logic — no HTTP imports)

```
services/
├── __init__.py                      # ⏳ Empty
├── exceptions.py                    # ⏳ ServiceError base + NotFoundError, AlreadyExistsError, ValidationError, PermissionDeniedError, InsufficientCreditsError
├── auth_service.py                  # ⏳ Login flow, password reset code lifecycle (impl-time decision: Redis vs ad-hoc table), token issuance/verification helpers
├── user_service.py                  # ⏳ create_user, authenticate, get_by_email, update_profile, admin_reset_password, normalize_email, hash/verify password
├── exam_service.py                  # ⏳ Exam CRUD, publish/unpublish (with question-count check), soft delete (set deleted_at), hard delete (CASCADE)
├── question_service.py              # ⏳ Question CRUD with per-type validation of question_data, soft/hard delete, Excel import parsing
├── attempt_service.py               # ⏳ Start attempt (enforce tier limits via COUNT), submit + auto-grading per question type, history queries
├── subscription_service.py          # ⏳ Get/update subscription, period reset logic, attempt limit check, credit deduction
└── subscription_plans.py            # ⏳ Static SUBSCRIPTION_PLANS dict — PlanTier enum, SubscriptionPlan dataclass, feature matrix (Free/Basic/Pro/Ultra)
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
├── __init__.py                      # ⏳ Empty
├── jwt_utils.py                     # ⏳ TokenType constants, create_access_token, create_refresh_token, decode_token (with type verification)
├── password_utils.py                # ⏳ hash_password, verify_password (bcrypt cost 12)
├── grading_utils.py                 # ⏳ Per-question-type grading: multiple_choice (index match), fill_blank (string match w/ case_sensitive), matching (pair compare), image_choice (index match)
└── excel_utils.py                   # ⏳ Excel-to-questions parser (openpyxl), maps spreadsheet rows to question_data shapes
```

## Top-level Dependencies (FastAPI DI)

```
dependencies.py                      # ⏳ get_current_user (Bearer JWT validator), require_admin, require_teacher_or_admin, require_subscription_tier (factory)
```

## Database Migrations

> The initial schema lives in [`schema.sql`](schema.sql). Apply it with [`scripts/init_schema.py`](scripts/init_schema.py) (recommended) or by pasting into the Supabase SQL Editor manually. If future schema changes accumulate, introduce a real migration tool (Alembic) under `migrations/` at that point — not before.

```
scripts/
├── __init__.py                      # ✅ Empty package marker
└── init_schema.py                   # ✅ Apply schema.sql via asyncpg. Flags: --check, --drop, -y. Used for fresh setup + dev resets.
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
    └── backend.yml                  # ✅ smoke (install + import) → deploy (Render API call, needs RENDER_API_KEY + RENDER_SERVICE_ID secrets). Lint + pytest jobs to be added in B3.
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
