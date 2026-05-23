# Mai Chi English - Backend Refactoring Plan

> **Scope**: Backend-only plan, derived from `MAICHIENGLISH_REFACTORING_PLAN.md`.
> Decisions locked in:
> - **Database**: **Fresh Supabase project (PostgreSQL 17)** — schema created from scratch. No migration from any prior project.
> - **Auth**: Custom JWT (no Supabase Auth). `password_hash` is part of `profiles` from day 1.
> - **Storage**: Supabase Storage (audio/images), within the same new project.
> - **Deployment Platform**: Render (Singapore region) — best latency for Vietnam-based users.

## Document Information

| Field | Value |
|-------|-------|
| **Project** | Mai Chi English Center - Online Testing System (Backend) |
| **Document Type** | Backend Requirements + High-Level Design (HLD) |
| **Version** | 1.0 |
| **Date** | May 2026 |
| **Target Audience** | Backend Developer, Architect Advisor |

---

## Executive Summary

This document outlines the backend portion of the Mai Chi English system: a **FastAPI (Python 3.14)** backend with independent JWT-based authentication and subscription management, backed by a new Supabase project running PostgreSQL 17.

### Current State
- **Architecture**: Monolithic Next.js 14 with Server Actions
- **Database**: Supabase (PostgreSQL)
- **Auth**: Supabase Auth (session-based)
- **Roles**: `student`, `admin`

### Target State (Backend)
- **Backend**: FastAPI on Python 3.14
- **Database**: PostgreSQL 17 (new Supabase project)
- **Auth**: Custom JWT tokens (access + refresh)
- **Roles**: `student`, `teacher`, `admin` (Ultra-equivalent), `parent`
- **Subscriptions**: Free, Basic, Pro, Ultra (Admin)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Backend Requirements](#2-backend-requirements)
3. [Database Schema](#3-database-schema)
4. [API Specification](#4-api-specification)
5. [Authentication & Authorization](#5-authentication--authorization)
6. [Subscription Tiers](#6-subscription-tiers)
7. [Implementation Phases (Backend)](#7-implementation-phases-backend)
8. [File Structure (Backend)](#8-file-structure-backend)
9. [Setup Checklist (Backend)](#9-setup-checklist-backend)
10. [Coding Standards and Patterns](#10-coding-standards-and-patterns)
11. [CI/CD Pipeline Setup (Backend)](#11-cicd-pipeline-setup-backend)

---

## 1. Architecture Overview

### Target Architecture (Backend View)

```
                                     ┌──────────────────────────┐
                                     │     FastAPI Backend      │
                       HTTP / REST   │                          │
   (Next.js Frontend) ◄────────────► │  ┌────────────────────┐  │
                       Bearer JWT    │  │  API Routes        │  │
                                     │  │  - /api/auth/*     │  │
                                     │  │  - /api/exams/*    │  │
                                     │  │  - /api/users/*    │  │
                                     │  │  - /api/admin/*    │  │
                                     │  └─────────┬──────────┘  │
                                     │            │             │
                                     │  ┌─────────▼──────────┐  │
                                     │  │  Services Layer    │  │
                                     │  │  - auth_service    │  │
                                     │  │  - exam_service    │  │
                                     │  │  - user_service    │  │
                                     │  │  - subscription_svc│  │
                                     │  └─────────┬──────────┘  │
                                     │            │             │
                                     │            ▼             │
                                     │  ┌────────────────────┐  │
                                     │  │   PostgreSQL DB    │  │
                                     │  │   (Supabase)       │  │
                                     │  └────────────────────┘  │
                                     │                          │
                                     │  ┌────────────────────┐  │
                                     │  │   Supabase Storage │  │
                                     │  │   (Audio/Images)   │  │
                                     │  └────────────────────┘  │
                                     │                          │
                                     │  Port: 8000              │
                                     └──────────────────────────┘
```

### Key Changes (Backend)

| Aspect | Before | After |
|--------|--------|-------|
| Backend | Server Actions (Next.js) | FastAPI (Python) |
| Auth | Supabase Auth (cookies) | Custom JWT (Bearer tokens) |
| API Style | Direct function calls | REST API endpoints |
| Deployment | Single Vercel app | Backend on Render (Docker) |
| Scalability | Limited | Horizontal scaling possible |

---

## 2. Backend Requirements

### 2.1 Technology Stack

| Component | Technology | Pinned Version (May 2026) |
|-----------|------------|---------------------------|
| Framework | FastAPI | `>=0.136,<0.137` |
| Python | CPython (GIL build, not free-threaded) | `3.14.x` |
| Database | PostgreSQL (via new Supabase project) | `17.x` |
| ORM / DB driver | SQLAlchemy 2.0 or asyncpg | SQLAlchemy `>=2.0.49,<2.1`, asyncpg `>=0.31,<1` |
| Auth | PyJWT + bcrypt | PyJWT `>=2.12,<3`, bcrypt `>=4.2,<5` |
| Validation | Pydantic v2 + email-validator | pydantic `>=2.13,<3`, email-validator `>=2.0,<3` |
| Settings | pydantic-settings | `>=2.7,<3` |
| ASGI Server | Uvicorn | `>=0.47,<0.48` |

### 2.2 Required API Modules

```
maichienglish-be/
├── api/
│   ├── auth/           # Authentication endpoints
│   ├── exams/          # Exam CRUD + publishing
│   ├── questions/      # Question management
│   ├── attempts/       # Student exam attempts
│   ├── users/          # User profile management
│   ├── admin/          # Admin-only operations
│   └── subscriptions/  # Subscription management
├── services/           # Business logic layer
├── models/             # SQLAlchemy models (optional)
├── utils/              # JWT, password hashing, grading, Excel import
└── config/             # Settings, database, logging
```

> Pydantic request/response schemas live alongside their routes
> (`api/<domain>/schemas.py`) — no separate root `schemas/` folder.
> See §8 for the full tree and [`PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md)
> for current file-by-file status.

### 2.3 Functional Requirements

#### Authentication
- [x] Email + password login
- [x] JWT access tokens (15-60 min expiry)
- [x] JWT refresh tokens (7-30 days expiry)
- [x] Password reset via email code
- [x] Token refresh endpoint
- [x] Token verification endpoint

#### User Management
- [x] Admin creates student accounts (no self-registration by students)
- [x] Admin creates teacher accounts
- [x] Admin creates parent accounts (1 parent shared by both spouses)
- [x] Admin links a student to a parent (1:N parent → students)
- [x] Password reset by admin
- [x] User profile CRUD
- [x] Role-based access (student, teacher, admin, parent)

#### Subscription Management (NEW)
- [x] Subscription tiers: Free, Basic, Pro, Ultra
- [x] Feature flags per tier
- [x] Credit system (for future AI features)
- [x] Admin (Ultra) can manage all subscriptions

#### Exam Management
- [x] CRUD for exams
- [x] Publish/unpublish toggle
- [x] Question CRUD (3 types: multiple_choice, fill_blank, matching)
- [ ] Excel import for questions (deferred to B3.4b — column format pending client confirmation)
- [x] Audio file management (storage buckets created, audio_url field on exams, audio-play counter enforcing `max_audio_plays`)

#### Attempt Management
- [x] Start exam attempt
- [x] Submit answers
- [x] Auto-grading
- [x] View results
- [x] History tracking

---

## 3. Database Schema

> **Delete semantics for exams & questions**: the default `DELETE` endpoint behavior is **soft delete** (set `deleted_at = now()`, all queries filter `WHERE deleted_at IS NULL`). A separate admin-only **hard delete** path runs an actual `DELETE` row, which `CASCADE`s through `attempts` and `answers` — used only to remove broken/erroneous content.
>
> Schema is reconciled with the prior Supabase project's tables (preserving domain features like listening audio + reading passage + per-question points) and extended with refactor-only additions (custom JWT auth, subscriptions, soft delete, `ON DELETE CASCADE`). Run the SQL in [`schema.sql`](../schema.sql).

### 3.1 `profiles` — users + auth

`parent_id` is a self-FK used to link a `student` row to its `parent` row. One parent can be linked to many students (1:N from parent → students); each student has at most one parent. The column is `NULL` for non-student roles and for students without a registered parent.

```sql
CREATE TABLE public.profiles (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL UNIQUE,
  password_hash  text NOT NULL,
  full_name      text NOT NULL,
  phone          text,
  role           text NOT NULL DEFAULT 'student'
                  CHECK (role IN ('student', 'teacher', 'admin', 'parent')),
  parent_id      uuid REFERENCES public.profiles(id) ON DELETE SET NULL,  -- only set when role='student'
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

### 3.2 `subscriptions` — one active row per user

```sql
CREATE TABLE public.subscriptions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               uuid NOT NULL UNIQUE
                          REFERENCES public.profiles(id) ON DELETE CASCADE,
  tier                  text NOT NULL DEFAULT 'free'
                          CHECK (tier IN ('free', 'basic', 'pro', 'ultra')),
  status                text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'canceled', 'expired')),
  credits_monthly       int  NOT NULL DEFAULT 0,
  credits_remaining     int  NOT NULL DEFAULT 0,
  current_period_start  timestamptz NOT NULL DEFAULT now(),
  current_period_end    timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
```

### 3.3 `password_reset_codes` — short-lived 6-digit codes

Used by `POST /api/auth/password/request-code` and `POST /api/auth/password/reset`. The plaintext code is sent to the user (eventually by email — for now returned in the API response in dev); only its bcrypt hash is persisted. Default lifetime 10 minutes.

```sql
CREATE TABLE public.password_reset_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
```

### 3.4 `exams` — exam definitions

`audio_url` + `max_audio_plays` apply to listening exams (one shared audio per exam, with a play-count limit enforced via `attempts.audio_play_count`). `passage` applies to reading exams.

```sql
CREATE TABLE public.exams (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title             text NOT NULL,
  level             text NOT NULL
                      CHECK (level IN ('primary', 'secondary', 'KET', 'PET', 'IELTS')),
  skill             text NOT NULL
                      CHECK (skill IN ('listening', 'reading')),
  duration_minutes  int  NOT NULL DEFAULT 45 CHECK (duration_minutes > 0),
  description       text,
  audio_url         text,                              -- listening: one shared audio file
  passage           text,                              -- reading: passage text
  max_audio_plays   int  NOT NULL DEFAULT 3,           -- listening: cap on student replays
  is_published      boolean NOT NULL DEFAULT false,
  created_by        uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz                        -- soft delete; NULL = active
);
```

### 3.5 `questions` — flexible per type via JSONB

`question_data` shape varies by `question_type`:

- `multiple_choice`: `{ "options": ["A","B","C","D"], "correct_index": 2 }`
- `fill_blank`: `{ "correct_answers": ["nine", "9"], "case_sensitive": false }`
- `matching`: `{ "left": [...], "right": [...], "correct_pairs": [[0,1],[1,0],[2,2]] }`

```sql
CREATE TABLE public.questions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  exam_id        uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  position       int  NOT NULL,
  question_type  text NOT NULL
                  CHECK (question_type IN ('multiple_choice', 'fill_blank', 'matching')),
  question_data  jsonb NOT NULL,
  points         int  NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz                           -- soft delete; NULL = active
);
```

### 3.6 `attempts` — one row per exam attempt

```sql
CREATE TABLE public.attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  exam_id              uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  score                numeric(6,2),
  total_points         numeric(6,2),
  percentage           numeric(5,2),
  time_spent_seconds   int,
  audio_play_count     int NOT NULL DEFAULT 0,         -- listening: tracks plays for limit enforcement
  started_at           timestamptz NOT NULL DEFAULT now(),
  submitted_at         timestamptz
);
```

### 3.7 `answers` — one row per question per attempt

```sql
CREATE TABLE public.answers (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id      uuid NOT NULL REFERENCES public.attempts(id) ON DELETE CASCADE,
  question_id     uuid NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  student_answer  jsonb,                               -- nullable: student may skip a question
  is_correct      boolean,
  points_earned   int NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);
```

### 3.8 Storage buckets (Supabase Storage)

Create two buckets in the new Supabase project:

| Bucket | Purpose |
|--------|---------|
| `audio` | Listening exam audio files (`.mp3`, `.m4a`) — referenced by `exams.audio_url` |
| `images` | Image assets for questions (`.png`, `.jpg`, `.webp`) — referenced inside `questions.question_data` |

---

## 4. API Specification

> **Response envelope**: every success response follows §10.10 — `{"status": <int>, "data": {...}}`. Errors return `{"detail": "<message>"}` via FastAPI `HTTPException`. Endpoints that return `204 No Content` (e.g. admin delete) have an empty body and no envelope.

### 4.1 Authentication Endpoints

#### POST /api/auth/login
Login and receive JWT tokens.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "fullName": "Nguyen Van A",
      "role": "student",
      "subscription": {
        "tier": "basic",
        "creditsRemaining": 5000
      }
    },
    "token": {
      "accessToken": "eyJhbGciOi...",
      "refreshToken": "eyJhbGciOi...",
      "expiresIn": 3600
    }
  }
}
```

**Error Response (401):**
```json
{
  "detail": "Email hoặc mật khẩu không đúng"
}
```

#### POST /api/auth/refresh
Refresh access token.

**Request:**
```json
{
  "refreshToken": "eyJhbGciOi..."
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "token": {
      "accessToken": "eyJhbGciOi...",
      "refreshToken": "eyJhbGciOi...",
      "expiresIn": 3600
    }
  }
}
```

#### POST /api/auth/verify
Verify an access token and return the decoded user/claims. Frontend uses this to check token validity on page load.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "valid": true,
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "role": "student",
      "tier": "basic"
    }
  }
}
```

**Error Response (401):**
```json
{
  "detail": "Invalid or expired token"
}
```

#### POST /api/auth/password/request-code
Request a 6-digit password-reset code. Lifetime: 10 minutes.

The response **always** returns `200` regardless of whether the email exists (anti-enumeration). Until an email provider is wired in (B3.6c), the code is returned inline as `devCode`; that field disappears once real email delivery is enabled.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "message": "Code sent",
    "expiresIn": 600,
    "devCode": "412903"
  }
}
```

#### POST /api/auth/password/reset
Reset password with a code obtained via `/password/request-code`.

**Request:**
```json
{
  "email": "user@example.com",
  "code": "412903",
  "newPassword": "newPassword123"
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "message": "Password reset successful"
  }
}
```

**Error Response (400):**
```json
{
  "detail": "Invalid or expired code"
}
```

### 4.2 User Endpoints

#### GET /api/users/me
Get current user profile.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "fullName": "Nguyen Van A",
      "role": "student",
      "phone": "0901234567",
      "subscription": {
        "tier": "basic",
        "status": "active",
        "creditsMonthly": 10000,
        "creditsRemaining": 7500
      },
      "createdAt": "2026-01-15T10:30:00Z"
    }
  }
}
```

#### PUT /api/users/me
Update the current user's own profile fields. Only `fullName` and `phone` are editable here; email + role + subscription are admin-managed.

**Headers:** `Authorization: Bearer <token>`

**Request (any subset of fields):**
```json
{
  "fullName": "Nguyen Van A",
  "phone": "0901234567"
}
```

**Response (200):** same shape as `GET /api/users/me`.

### 4.3 Admin Endpoints

#### GET /api/admin/users
List users (admin only). Most recent first.

**Query Params:**
- `role`: optional filter — `student` / `teacher` / `admin` / `parent`
- `page`: 1-based page number (default 1)
- `limit`: page size (default 50, max 100)

**Response (200):** uses the paginated-list envelope from §10.10 (`items` + `pagination`). Subscription data is nested per the same convention used by `/api/auth/login` and `/api/users/me`.
```json
{
  "status": 200,
  "data": {
    "items": [
      {
        "id": "uuid",
        "email": "student@example.com",
        "fullName": "Nguyen Van B",
        "role": "student",
        "phone": "0909876543",
        "subscription": {
          "tier": "basic",
          "status": "active",
          "creditsMonthly": 10000,
          "creditsRemaining": 7500
        },
        "parentId": "uuid-or-null",
        "createdAt": "2026-05-15T10:30:00Z"
      }
    ],
    "pagination": {
      "page": 1,
      "limit": 50,
      "total": 134,
      "totalPages": 3
    }
  }
}
```

#### POST /api/admin/users
Create new user (admin only). `role` may be `student`, `teacher`, `admin`, or `parent`. `parentId` is optional and only honored when `role === "student"`; it must reference an existing profile whose role is `parent`.

**Request:**
```json
{
  "email": "student@example.com",
  "password": "tempPassword123",
  "fullName": "Nguyen Van B",
  "role": "student",
  "phone": "0909876543",
  "subscriptionTier": "basic",
  "parentId": "uuid-of-existing-parent"
}
```

#### DELETE /api/admin/users/{user_id}
Delete user (admin only).

#### POST /api/admin/users/{user_id}/reset-password
Reset user password (admin only).

**Request:**
```json
{
  "newPassword": "newTempPassword123"
}
```

#### PUT /api/admin/users/{student_id}/parent
Link or unlink a parent on a student (admin only). The target user must be a `student`; the referenced `parentId` (if non-null) must be a `parent`. Pass `parentId: null` to unlink.

**Request:**
```json
{
  "parentId": "uuid-of-existing-parent"
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "user": {
      "id": "uuid",
      "role": "student",
      "parentId": "uuid-of-existing-parent"
    }
  }
}
```

### 4.4 Exam Endpoints

#### GET /api/exams
List exams (filtered by role: students see published only).

**Query Params:**
- `level`: Filter by level (primary, secondary, KET, PET, IELTS)
- `skill`: Filter by skill (listening, reading)
- `published`: true/false (admin / teacher only)

**Response (200):** uses the list envelope from §10.10 (`items`).

#### GET /api/exams/{exam_id}
Get a single exam by id. Non-privileged users (student / parent) only see published exams; any attempt to read an unpublished exam returns 404.

#### POST /api/exams (admin only)
Create new exam. Returns 201 with the created exam wrapped under `data.exam`.

#### PUT /api/exams/{exam_id} (admin only)
Update exam. Partial — pass only the fields you want to change.

#### POST /api/exams/{exam_id}/publish (admin only)
Publish exam (requires at least 1 active question; otherwise 400).

#### POST /api/exams/{exam_id}/unpublish (admin only)
Unpublish exam. Hides it from students without deleting.

#### DELETE /api/exams/{exam_id} (admin only)
Soft-delete an exam: sets `deleted_at`, also forces `is_published=false`. Data preserved.

#### DELETE /api/exams/{exam_id}/hard (admin only)
Hard-delete an exam. Runs a real `DELETE` row — `CASCADE`s through questions, attempts, and answers. Use only for broken/erroneous content.

### 4.5 Question Endpoints

#### GET /api/exams/{exam_id}/questions
List questions for an exam, ordered by `position`. Non-privileged users only see questions of published exams. Response uses the list envelope from §10.10 (`items`).

#### POST /api/exams/{exam_id}/questions (admin only)
Add a question. `question_data` is validated server-side per `question_type` (multiple_choice / fill_blank / matching). If `position` is omitted, the server assigns `MAX(position)+1` within the exam.

#### GET /api/questions/{question_id}
Get a single question by id. Non-privileged users only see questions of published exams.

#### PUT /api/questions/{question_id} (admin only)
Update a question. Changing `question_type` requires also supplying a matching `question_data`.

#### DELETE /api/questions/{question_id} (admin only)
Soft-delete a question (sets `deleted_at`).

#### DELETE /api/questions/{question_id}/hard (admin only)
Hard-delete a question. `CASCADE`s through answers.

#### POST /api/exams/{exam_id}/questions/import (admin only)
Import questions from Excel. ⏳ **Deferred to B3.4b** — column format pending client confirmation.

### 4.6 Attempt Endpoints

#### POST /api/attempts
Start new exam attempt.

**Request:**
```json
{
  "examId": "uuid"
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "attemptId": "uuid",
    "exam": { ... },
    "questions": [ ... ],
    "startedAt": "2026-05-12T10:00:00Z"
  }
}
```

#### POST /api/attempts/{attempt_id}/submit
Submit answers and get score.

**Request:**
```json
{
  "answers": [
    { "questionId": "uuid", "studentAnswer": 2 },
    { "questionId": "uuid", "studentAnswer": "nine" },
    { "questionId": "uuid", "studentAnswer": [[0,1], [1,0], [2,2]] }
  ],
  "timeSpentSeconds": 1800
}
```

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "score": 8.5,
    "totalPoints": 10,
    "percentage": 85,
    "submittedAt": "2026-05-12T10:30:00Z"
  }
}
```

#### POST /api/attempts/{attempt_id}/audio-play
Increment the listening-audio play counter on an in-progress attempt. Only valid for listening exams and before submit. Rejects once `audio_play_count >= exams.max_audio_plays`.

**Headers:** `Authorization: Bearer <token>` (owner only)

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "audioPlayCount": 2,
    "maxAudioPlays": 3,
    "remainingPlays": 1
  }
}
```

**Error Response (403):** `"Not the owner of this attempt"` or `"Audio play limit reached (N)"`.
**Error Response (400):** `"Attempt already submitted"` or `"Audio playback only applies to listening exams"`.

#### GET /api/attempts/{attempt_id}
Get attempt details with per-question breakdown. Visible to owner, admin/teacher, or the parent linked to the owner. While the attempt is still in progress (no `submitted_at`), correct-answer fields inside `question_data` are stripped.

#### GET /api/attempts/history
Get the current user's attempt history (most recent first, capped at 100). Response uses the list envelope from §10.10 (`items`).

### 4.7 Subscription Endpoints

#### GET /api/subscriptions/me
Get current user's subscription.

#### GET /api/subscriptions/plans
Get available plans and features.

#### PUT /api/admin/subscriptions/{user_id} (admin only)
Update user subscription tier.

### 4.8 Parent Endpoints

All endpoints under `/api/parents/me/...` require `role=parent` and only operate on students linked to the authenticated parent via `profiles.parent_id`. Accessing another parent's children returns 403.

#### GET /api/parents/me/children
List students linked to the current parent.

**Response (200):**
```json
{
  "status": 200,
  "data": {
    "items": [
      {
        "id": "uuid",
        "email": "student@example.com",
        "fullName": "Nguyen Van B",
        "phone": "0909876543",
        "createdAt": "2026-05-15T10:30:00Z"
      }
    ]
  }
}
```

#### GET /api/parents/me/children/{student_id}/attempts
List a linked child's attempt history. Same response shape as `GET /api/attempts/history`.

#### GET /api/parents/me/children/{student_id}/attempts/{attempt_id}
Get a specific attempt for a linked child. Same response shape as `GET /api/attempts/{attempt_id}`.

---

## 5. Authentication & Authorization

### 5.1 JWT Token Structure

**Access Token Claims:**
```json
{
  "sub": "user@example.com",
  "type": "access",
  "role": "student",
  "tier": "basic",
  "iat": 1715500000,
  "exp": 1715503600,
  "iss": "maichienglish",
  "aud": "maichienglish"
}
```

**Refresh Token Claims:**
```json
{
  "sub": "user@example.com",
  "type": "refresh",
  "iat": 1715500000,
  "exp": 1716104800,
  "iss": "maichienglish",
  "aud": "maichienglish"
}
```

### 5.2 Password Hashing

Use **bcrypt** with cost factor 12 (matching cloudmortgage pattern):

```python
import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
```

### 5.3 Role-Based Access Control

| Role | Permissions |
|------|-------------|
| `student` | View published exams, take exams, view own results |
| `parent` | View profile + attempt history of linked students (via `profiles.parent_id`); cannot take exams |
| `teacher` | All student permissions + view all results, export data |
| `admin` | All permissions (Ultra tier), manage users, parent ↔ student links, subscriptions |

### 5.4 FastAPI Dependencies

```python
# dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

oauth2_scheme = HTTPBearer()

def get_current_user(token: HTTPAuthorizationCredentials = Depends(oauth2_scheme)):
    """Validate JWT and return user payload."""
    try:
        payload = decode_token(token.credentials, verify_type=TokenType.ACCESS)
        return payload
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def require_admin(current_user: dict = Depends(get_current_user)):
    """Require admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

def require_teacher_or_admin(current_user: dict = Depends(get_current_user)):
    """Require teacher or admin role."""
    if current_user.get("role") not in ["teacher", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required"
        )
    return current_user
```

---

## 6. Subscription Tiers

### 6.1 Tier Definitions

| Tier | Price | Target User | Key Features |
|------|-------|-------------|--------------|
| **Free** | $0 | Trial students | 5 attempts/month, basic reports |
| **Basic** | $9.99/mo | Regular students | 50 attempts/month, full history |
| **Pro** | $19.99/mo | Power users | Unlimited attempts, analytics |
| **Ultra** | $0 (Admin) | Teachers/Admins | All features, user management |

### 6.2 Feature Matrix

| Feature | Free | Basic | Pro | Ultra (Admin) |
|---------|------|-------|-----|---------------|
| View published exams | ✅ | ✅ | ✅ | ✅ |
| Take exams | 5/month | 50/month | Unlimited | Unlimited |
| View own results | ✅ | ✅ | ✅ | ✅ |
| Full history | Last 5 | ✅ | ✅ | ✅ |
| Progress charts | ❌ | ✅ | ✅ | ✅ |
| Export results | ❌ | ❌ | ✅ | ✅ |
| View all students | ❌ | ❌ | ❌ | ✅ |
| Manage exams | ❌ | ❌ | ❌ | ✅ |
| Manage users | ❌ | ❌ | ❌ | ✅ |
| Manage subscriptions | ❌ | ❌ | ❌ | ✅ |

### 6.3 Subscription Plan Implementation

```python
# services/subscription_plans.py
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional

class PlanTier(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ULTRA = "ultra"

@dataclass
class PlanFeature:
    key: str
    name: str
    active: bool
    value: Optional[str] = None

@dataclass
class SubscriptionPlan:
    tier: PlanTier
    name: str
    price_monthly: float
    attempts_monthly: int  # -1 for unlimited
    features: List[PlanFeature]

SUBSCRIPTION_PLANS = {
    PlanTier.FREE: SubscriptionPlan(
        tier=PlanTier.FREE,
        name="Free",
        price_monthly=0.0,
        attempts_monthly=5,
        features=[
            PlanFeature(key="take_exams", name="Take Exams", active=True, value="5/month"),
            PlanFeature(key="view_results", name="View Results", active=True),
            PlanFeature(key="full_history", name="Full History", active=False),
            PlanFeature(key="progress_charts", name="Progress Charts", active=False),
            PlanFeature(key="export_results", name="Export Results", active=False),
        ],
    ),
    PlanTier.BASIC: SubscriptionPlan(
        tier=PlanTier.BASIC,
        name="Basic",
        price_monthly=9.99,
        attempts_monthly=50,
        features=[
            PlanFeature(key="take_exams", name="Take Exams", active=True, value="50/month"),
            PlanFeature(key="view_results", name="View Results", active=True),
            PlanFeature(key="full_history", name="Full History", active=True),
            PlanFeature(key="progress_charts", name="Progress Charts", active=True),
            PlanFeature(key="export_results", name="Export Results", active=False),
        ],
    ),
    PlanTier.PRO: SubscriptionPlan(
        tier=PlanTier.PRO,
        name="Pro",
        price_monthly=19.99,
        attempts_monthly=-1,  # Unlimited
        features=[
            PlanFeature(key="take_exams", name="Take Exams", active=True, value="Unlimited"),
            PlanFeature(key="view_results", name="View Results", active=True),
            PlanFeature(key="full_history", name="Full History", active=True),
            PlanFeature(key="progress_charts", name="Progress Charts", active=True),
            PlanFeature(key="export_results", name="Export Results", active=True),
        ],
    ),
    PlanTier.ULTRA: SubscriptionPlan(
        tier=PlanTier.ULTRA,
        name="Ultra (Admin)",
        price_monthly=0.0,  # Free for admins
        attempts_monthly=-1,
        features=[
            PlanFeature(key="take_exams", name="Take Exams", active=True, value="Unlimited"),
            PlanFeature(key="view_results", name="View Results", active=True),
            PlanFeature(key="full_history", name="Full History", active=True),
            PlanFeature(key="progress_charts", name="Progress Charts", active=True),
            PlanFeature(key="export_results", name="Export Results", active=True),
            PlanFeature(key="manage_exams", name="Manage Exams", active=True),
            PlanFeature(key="manage_users", name="Manage Users", active=True),
            PlanFeature(key="manage_subscriptions", name="Manage Subscriptions", active=True),
            PlanFeature(key="view_all_students", name="View All Students", active=True),
        ],
    ),
}
```

---

## 7. Implementation Phases (Backend)

### Phase 1: Backend Foundation (Week 1-2)

1. **Supabase Project Bootstrap** *(do this first — everything else depends on it)*
   - [ ] Create new Supabase project in **Singapore (`ap-southeast-1`)** with PostgreSQL 17
   - [ ] Copy `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
   - [ ] Run the schema SQL from `3. Database Schema` in the Supabase SQL Editor
   - [ ] Create Supabase Storage buckets: `audio`, `images`

2. **Project Setup**
   - [ ] Create FastAPI project structure
   - [ ] Configure Pydantic settings (DB URL, JWT secret, etc.)
   - [ ] Set up database connection (asyncpg or SQLAlchemy)
   - [ ] Configure CORS for frontend

3. **Authentication Module**
   - [ ] Implement JWT token generation (access + refresh)
   - [ ] Implement password hashing (bcrypt)
   - [ ] Create `/api/auth/login` endpoint
   - [ ] Create `/api/auth/refresh` endpoint
   - [ ] Create `/api/auth/password/*` endpoints
   - [ ] Create `get_current_user` dependency

4. **User Management**
   - [ ] Create `/api/users/me` endpoint
   - [ ] Create admin user creation endpoint
   - [ ] Implement role-based access control

### Phase 2: Core Features (Week 3-4)

1. **Exam Management**
   - [ ] CRUD endpoints for exams
   - [ ] Publish/unpublish logic
   - [ ] Question CRUD endpoints
   - [ ] Excel import endpoint

2. **Attempt Management**
   - [ ] Create attempt endpoint
   - [ ] Submit answers + grading
   - [ ] Get attempt details
   - [ ] History endpoint

### Phase 3: Subscription & Polish (Week 5-6)

1. **Subscription System**
   - [ ] Implement subscription plans
   - [ ] Create subscription endpoints
   - [ ] Add feature access checks
   - [ ] Implement attempt limits

2. **Testing & Documentation**
   - [ ] Write unit tests for auth
   - [ ] Write API documentation
   - [ ] Add request validation

---

## 8. File Structure (Backend)

> Per-domain Pydantic schemas live next to their routes (`api/<domain>/schemas.py`), not in a separate root `schemas/` folder. See [`PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) for the current per-file status and notes.

```
maichienglish-be/
├── main.py                         # FastAPI app entry point
├── dependencies.py                 # FastAPI DI: get_current_user, require_admin, etc.
├── requirements.txt
├── Dockerfile
├── render.yaml                     # Render deploy config (autoDeploy:false; gated by GHA)
├── schema.sql                      # Initial DB schema (run via scripts/init_schema.py)
├── .env.example
├── .github/
│   └── workflows/
│       └── backend.yml             # CI: smoke import + Render deploy on main
│
├── config/
│   ├── __init__.py
│   ├── settings.py                 # Pydantic settings (env vars)
│   ├── database.py                 # asyncpg pool lifecycle
│   └── logging.py                  # setup_logging() helper
│
├── api/
│   ├── __init__.py                 # Empty (each subpackage exposes its own router)
│   ├── auth/
│   │   ├── __init__.py             # Re-exports `router`
│   │   ├── routes.py               # Auth endpoints
│   │   └── schemas.py              # Auth Pydantic models
│   ├── users/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── admin/
│   │   ├── __init__.py
│   │   ├── routes.py               # Admin-only endpoints (require_admin at router level)
│   │   └── schemas.py
│   ├── exams/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── questions/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── attempts/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── schemas.py
│   └── subscriptions/
│       ├── __init__.py
│       ├── routes.py
│       └── schemas.py
│
├── services/
│   ├── __init__.py
│   ├── exceptions.py               # ServiceError base + typed subclasses
│   ├── auth_service.py             # Password reset code lifecycle (B3.6)
│   ├── user_service.py             # User CRUD + authenticate
│   ├── exam_service.py             # Exam CRUD
│   ├── question_service.py
│   ├── attempt_service.py          # Attempts + grading
│   ├── subscription_service.py
│   └── subscription_plans.py       # Static plan catalog (Free / Basic / Pro / Ultra)
│
├── models/                          # SQLAlchemy models (optional — only if ORM is adopted)
│   ├── __init__.py
│   ├── user.py
│   ├── exam.py
│   ├── question.py
│   ├── attempt.py
│   └── subscription.py
│
├── utils/
│   ├── __init__.py
│   ├── jwt_utils.py                # Token generation/validation
│   ├── password_utils.py           # Bcrypt hashing
│   ├── grading_utils.py            # Grade multiple_choice / fill_blank / matching
│   └── excel_utils.py              # Excel import
│
├── scripts/
│   ├── __init__.py
│   ├── init_schema.py              # Apply schema.sql via asyncpg (--check / --drop / -y)
│   └── seed_admin.py               # One-shot create first admin from ADMIN_* env vars
│
└── tests/                           # pytest suite (B3.6)
    ├── __init__.py
    ├── conftest.py
    ├── test_auth.py
    ├── test_users.py
    ├── test_exams.py
    ├── test_questions.py
    ├── test_attempts.py
    └── test_subscriptions.py
```

---

## 9. Setup Checklist (Backend)

> Greenfield setup — no migration. Follow top to bottom.

### Step 1 — Supabase project

- [ ] Create new Supabase project, region **Singapore (`ap-southeast-1`)**
- [ ] Confirm PostgreSQL version is **17**
- [ ] Copy from project settings:
  - [ ] Connection string → `DATABASE_URL`
  - [ ] `SUPABASE_URL`
  - [ ] `service_role` key → `SUPABASE_SERVICE_ROLE_KEY`
- [ ] Run the schema SQL from `3. Database Schema` in the SQL Editor
- [ ] Create Storage buckets: `audio`, `images`

### Step 2 — Backend application

- [ ] Create FastAPI project with the structure in `8. File Structure`
- [ ] Implement the modules per `7. Implementation Phases`
- [ ] Test auth endpoints with Postman

### Step 3 — Deploy

- [ ] Push backend repo to GitHub (private)
- [ ] Create Render service (Docker, region: Singapore), wire env vars
- [ ] First deploy from `main`
- [ ] Verify `/health` returns 200 from Render's public URL

---

## Environment Variables (Backend)

### Backend (.env)

```env
# Database
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres

# JWT
JWT_SECRET_KEY=your-very-long-secret-key-at-least-32-characters
JWT_ACCESS_EXP_MINUTES=60
JWT_REFRESH_EXP_DAYS=7
JWT_ALGORITHM=HS256
JWT_ISSUER=maichienglish
JWT_AUDIENCE=maichienglish

# Supabase Storage (for audio/images)
SUPABASE_URL=https://[PROJECT].supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# CORS
CORS_ORIGINS=http://localhost:3000,https://maichienglish.vercel.app

# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false
```

---

## 10. Coding Standards and Patterns

> **IMPORTANT**: All code MUST follow the patterns established in the cloudmortgage.io codebase. This ensures consistency, maintainability, and easier code reviews.

### 10.1 Settings Configuration Pattern

Use **Pydantic BaseSettings** for all configuration. This provides:
- Type validation
- Environment variable loading
- Default values
- Easy testing

```python
# config/settings.py
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Configuration
    app_name: str = Field(default="Mai Chi English API", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    port: int = Field(default=8000, alias="PORT")

    # Database
    database_url: str = Field(..., alias="DATABASE_URL")  # Required

    # JWT Configuration - MUST have these fields
    jwt_secret_key: str = Field(default="change-me-in-prod", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_exp_minutes: int = Field(default=60, alias="JWT_ACCESS_EXP_MINUTES")
    jwt_refresh_exp_days: int = Field(default=7, alias="JWT_REFRESH_EXP_DAYS")
    jwt_issuer: str = Field(default="maichienglish", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="maichienglish", alias="JWT_AUDIENCE")

    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    # Supabase (for storage)
    supabase_url: Optional[str] = Field(default=None, alias="SUPABASE_URL")
    supabase_service_role_key: Optional[str] = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS from environment (comma-separated)."""
        if self.cors_origins:
            return [o.strip() for o in self.cors_origins.split(",")]
        return ["http://localhost:3000"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"
        populate_by_name = True


@lru_cache()
def get_settings() -> Settings:
    """
    Get the application settings instance (cached).

    Returns:
        Settings instance
    """
    settings = Settings()

    # Log JWT secret status (not the actual value for security)
    if settings.jwt_secret_key == "change-me-in-prod":
        logger.warning("JWT secret is still using default value 'change-me-in-prod'")
    else:
        logger.info(f"JWT secret loaded successfully (length: {len(settings.jwt_secret_key)} chars)")

    return settings
```

### 10.2 JWT Token Utilities Pattern

Create a dedicated `jwt_utils.py` with clear token types and functions:

```python
# utils/jwt_utils.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

from config.settings import get_settings

logger = logging.getLogger(__name__)


class TokenType:
    """Token type constants - use class not enum for simplicity."""
    ACCESS = "access"
    REFRESH = "refresh"


def create_token(
    email: str,
    token_type: str,
    expires_delta: timedelta,
    role: Optional[str] = None,
    tier: Optional[str] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """
    Create a JWT token with standard claims.

    Args:
        email: User email (becomes 'sub' claim)
        token_type: TokenType.ACCESS or TokenType.REFRESH
        expires_delta: Token expiration time
        role: User role (student, teacher, admin)
        tier: Subscription tier (free, basic, pro, ultra)
        extra_claims: Additional claims to include

    Returns:
        Encoded JWT token string
    """
    settings = get_settings()

    now = datetime.now(timezone.utc)

    payload = {
        "sub": email,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }

    if role:
        payload["role"] = role
    if tier:
        payload["tier"] = tier
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    email: str,
    role: str,
    tier: str,
    extra_claims: Optional[dict] = None,
) -> str:
    """Create access token with role and tier claims."""
    settings = get_settings()
    return create_token(
        email=email,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_exp_minutes),
        role=role,
        tier=tier,
        extra_claims=extra_claims,
    )


def create_refresh_token(email: str) -> str:
    """Create refresh token (minimal claims, longer expiry)."""
    settings = get_settings()
    return create_token(
        email=email,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_exp_days),
    )


def decode_token(token: str, verify_type: Optional[str] = None) -> dict:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string
        verify_type: If provided, verify the token type matches

    Returns:
        Decoded token payload

    Raises:
        jwt.InvalidTokenError: If token is invalid or expired
        ValueError: If token type doesn't match verify_type
    """
    settings = get_settings()

    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )

    if verify_type and payload.get("type") != verify_type:
        raise ValueError(f"Invalid token type. Expected {verify_type}, got {payload.get('type')}")

    return payload
```

### 10.3 Service Class Pattern

Services encapsulate business logic. Follow this pattern:

```python
# services/user_service.py
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class UserAlreadyExistsError(Exception):
    """Raised when attempting to create a user that already exists."""
    pass


class InvalidCredentialsError(Exception):
    """Raised when login credentials are invalid."""
    pass


class InsufficientCreditsError(Exception):
    """Raised when user doesn't have enough credits."""
    pass


class UserService:
    """
    User management service.

    Handles user creation, authentication, and profile management.
    All database operations should go through this service.
    """

    def __init__(self, db_pool=None):
        """
        Initialize service with optional database pool.
        Uses lazy initialization if pool not provided.
        """
        self._db_pool = db_pool
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def db(self):
        """Lazy database initialization."""
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    def _normalize_email(self, email: str) -> str:
        """
        Normalize email address for consistent storage.

        - Lowercase
        - Strip whitespace
        - Remove plus-addressing (user+tag@domain.com -> user@domain.com)
        """
        import unicodedata

        email = email.strip().lower()
        email = unicodedata.normalize("NFKC", email)

        # Remove plus-addressing
        if "+" in email:
            local, domain = email.rsplit("@", 1)
            local = local.split("+")[0]
            email = f"{local}@{domain}"

        return email

    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt."""
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

    def _verify_password(self, password: str, hashed: str) -> bool:
        """Verify password against hash."""
        import bcrypt
        return bcrypt.checkpw(password.encode(), hashed.encode())

    async def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: str = "student",
        tier: str = "free",
    ) -> Dict[str, Any]:
        """
        Create a new user.

        Args:
            email: User email (will be normalized)
            password: Plain text password (will be hashed)
            full_name: User's full name
            role: User role (student, teacher, admin)
            tier: Subscription tier (free, basic, pro, ultra)

        Returns:
            Created user dict

        Raises:
            UserAlreadyExistsError: If email already exists
        """
        email = self._normalize_email(email)

        # Check if user exists
        existing = await self._get_user_by_email(email)
        if existing:
            raise UserAlreadyExistsError(f"User with email {email} already exists")

        password_hash = self._hash_password(password)
        now = datetime.now(timezone.utc)

        # Insert user logic here...
        self._logger.info(f"Created user: {email}, role: {role}, tier: {tier}")

        return {
            "email": email,
            "full_name": full_name,
            "role": role,
            "tier": tier,
            "created_at": now.isoformat(),
        }

    async def authenticate(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user by email and password.

        Args:
            email: User email
            password: Plain text password

        Returns:
            User dict if authentication successful

        Raises:
            InvalidCredentialsError: If credentials are invalid
        """
        email = self._normalize_email(email)

        user = await self._get_user_by_email(email)
        if not user:
            # Log for security audit but don't reveal if email exists
            self._logger.warning(f"Login attempt for non-existent user: {email}")
            raise InvalidCredentialsError("Invalid email or password")

        if not self._verify_password(password, user["password_hash"]):
            self._logger.warning(f"Failed login attempt for user: {email}")
            raise InvalidCredentialsError("Invalid email or password")

        self._logger.info(f"Successful login: {email}")
        return user

    async def _get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email. Returns None if not found."""
        # Database query implementation...
        pass


# Singleton instance for convenience
user_service = UserService()
```

### 10.4 API Routes Pattern

Follow this pattern for API routes:

```python
# api/auth/routes.py
import logging
from fastapi import APIRouter, HTTPException, status, Depends

from .schemas import LoginRequest, LoginResponse, TokenResponse
from services.user_service import user_service, InvalidCredentialsError
from utils.jwt_utils import create_access_token, create_refresh_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT tokens.

    - **email**: User email address
    - **password**: User password

    Returns access token (short-lived) and refresh token (long-lived).
    """
    try:
        user = await user_service.authenticate(
            email=request.email,
            password=request.password,
        )
    except InvalidCredentialsError:
        # Use generic message to prevent email enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không đúng",  # Vietnamese message
        )

    # Create tokens
    access_token = create_access_token(
        email=user["email"],
        role=user["role"],
        tier=user.get("tier", "free"),
    )
    refresh_token = create_refresh_token(email=user["email"])

    # Log for security audit (SOC 2 compliance)
    logger.info(f"User logged in: {user['email']}")

    return LoginResponse(
        status=200,
        data={
            "user": {
                "id": user["id"],
                "email": user["email"],
                "fullName": user["full_name"],
                "role": user["role"],
                "subscription": {
                    "tier": user.get("tier", "free"),
                    "creditsRemaining": user.get("credits_remaining", 0),
                },
            },
            "token": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresIn": 3600,  # 1 hour in seconds
            },
        },
    )
```

### 10.5 Pydantic Schemas Pattern

```python
# api/auth/schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any


class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=6, description="User password")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123",
            }
        }


class TokenData(BaseModel):
    """JWT token response data."""
    accessToken: str
    refreshToken: str
    expiresIn: int = Field(description="Access token expiry in seconds")


class UserSubscription(BaseModel):
    """User subscription info."""
    tier: str
    creditsRemaining: int = 0


class UserData(BaseModel):
    """User data in response."""
    id: str
    email: str
    fullName: str
    role: str
    subscription: UserSubscription


class LoginResponseData(BaseModel):
    """Login response data."""
    user: UserData
    token: TokenData


class LoginResponse(BaseModel):
    """Login response wrapper."""
    status: int = 200
    data: LoginResponseData


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
```

### 10.6 Dependencies Pattern

```python
# dependencies.py
import logging
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from utils.jwt_utils import decode_token, TokenType

logger = logging.getLogger(__name__)
oauth2_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    token: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
) -> dict:
    """
    Validate JWT token and return user payload.

    Use as FastAPI dependency:
        @router.get("/me")
        async def get_me(current_user: dict = Depends(get_current_user)):
            return current_user
    """
    try:
        payload = decode_token(token.credentials, verify_type=TokenType.ACCESS)
        return payload
    except Exception as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Require admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def require_teacher_or_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Require teacher or admin role."""
    role = current_user.get("role")
    if role not in ["teacher", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required",
        )
    return current_user


async def require_subscription_tier(
    required_tiers: list[str],
):
    """
    Factory for tier requirement dependency.

    Usage:
        @router.get("/premium-feature")
        async def premium(
            user: dict = Depends(require_subscription_tier(["pro", "ultra"]))
        ):
            ...
    """
    async def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        tier = current_user.get("tier", "free")
        if tier not in required_tiers:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires one of: {', '.join(required_tiers)}",
            )
        return current_user

    return dependency
```

### 10.7 Logging Standards

Use Python's built-in logging with consistent formatting:

```python
# config/logging.py
import logging
import sys

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging(level: str = "INFO"):
    """Configure application logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# Usage in modules:
logger = logging.getLogger(__name__)

# Good logging examples:
logger.info(f"User logged in: {email}")
logger.warning(f"Failed login attempt for: {email}")
logger.error(f"Database connection failed: {e}")

# BAD - Never use print() for logging:
# print("User logged in")  # ❌ DON'T DO THIS
```

### 10.8 Exception Handling Pattern

Define custom exceptions in services, catch in routes:

```python
# services/exceptions.py
class ServiceError(Exception):
    """Base exception for all service errors."""
    pass


class NotFoundError(ServiceError):
    """Resource not found."""
    pass


class AlreadyExistsError(ServiceError):
    """Resource already exists."""
    pass


class ValidationError(ServiceError):
    """Input validation failed."""
    pass


class PermissionDeniedError(ServiceError):
    """User lacks permission for this action."""
    pass


class InsufficientCreditsError(ServiceError):
    """User doesn't have enough credits."""
    pass


# In routes - convert service exceptions to HTTP exceptions:
from fastapi import HTTPException, status
from services.exceptions import NotFoundError, AlreadyExistsError

@router.get("/exams/{exam_id}")
async def get_exam(exam_id: str, current_user: dict = Depends(get_current_user)):
    try:
        exam = await exam_service.get_exam(exam_id)
        return exam
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam not found",
        )
```

### 10.9 Database Query Pattern

Use parameterized queries to prevent SQL injection:

```python
# services/exam_service.py

# GOOD - Parameterized query
async def get_exam_by_id(self, exam_id: str) -> Optional[dict]:
    query = """
        SELECT id, title, level, skill, duration_minutes, is_published
        FROM exams
        WHERE id = $1
    """
    result = await self.db.fetchrow(query, exam_id)
    return dict(result) if result else None


# BAD - SQL injection vulnerability:
# query = f"SELECT * FROM exams WHERE id = '{exam_id}'"  # ❌ NEVER DO THIS
```

### 10.10 API Response Format

All API responses follow these envelopes:

```python
# Success response — single resource or action result
{
    "status": 200,
    "data": {
        # Either a single named key (e.g. {"user": {...}}, {"exam": {...}})
        # or flat fields for action results (e.g. {"score": ..., "submittedAt": ...}).
    }
}

# Error response (handled by FastAPI HTTPException)
{
    "detail": "Error message here"
}

# List response — ALWAYS uses `items` (regardless of pagination).
# This lets the frontend write a generic parseList<T>() helper.
{
    "status": 200,
    "data": {
        "items": [...]
    }
}

# Paginated list response — same `items`, plus a `pagination` sibling
{
    "status": 200,
    "data": {
        "items": [...],
        "pagination": {
            "page": 1,
            "limit": 20,
            "total": 150,
            "totalPages": 8
        }
    }
}
```

### 10.11 File Organization Rules

1. **One service per domain**: `user_service.py`, `exam_service.py`, not `service.py`
2. **Schemas next to routes**: `api/auth/routes.py` and `api/auth/schemas.py`
3. **Utils are generic**: Password hashing, JWT, grading algorithms
4. **Services contain business logic**: No HTTP/FastAPI imports in services
5. **Config is separate**: All env vars in `config/settings.py`

### 10.12 Code Style Checklist

Before committing code, verify:

- [ ] All functions have docstrings explaining purpose, args, returns
- [ ] All exceptions are logged before being raised/re-raised
- [ ] No hardcoded secrets (use settings)
- [ ] No print() statements (use logger)
- [ ] Pydantic models have field descriptions
- [ ] API endpoints have summary/description in docstrings
- [ ] Service methods are async where doing I/O
- [ ] Email addresses are normalized before storage/comparison
- [ ] Passwords are hashed, never logged or stored in plain text
- [ ] All SQL uses parameterized queries

---

## 11. CI/CD Pipeline Setup (Backend)

> **Note**: This setup uses Supabase for database and storage. No AWS migration required.
> **Deployment Platform Chosen**: Render (Singapore region) — best latency for Vietnam-based users.

### 11.1 Repository Structure

**Backend Repository (separate from frontend)**

```
github.com/your-org/
└── maichienglish-api/         # FastAPI app → Deploy to Render
```

### 11.2 GitHub Repository Setup

#### Step 1: Create Repository

```bash
gh repo create maichienglish-api --private --clone
```

#### Step 2: Branch Protection Rules

Go to **Repository Settings > Branches > Add rule**:

- **Branch name pattern**: `main`
- **Require pull request before merging**: ✅
- **Require approvals**: 1
- **Require status checks to pass**: ✅
- **Require branches to be up to date**: ✅

#### Step 3: Set Up GitHub Secrets

Go to **Repository Settings > Secrets and variables > Actions**:

**Backend Repository Secrets:**
```
# For Render deployment
RENDER_API_KEY        # render.com/docs/api
RENDER_SERVICE_ID     # From Render dashboard

# Application secrets
DATABASE_URL          # Your Supabase PostgreSQL connection string
JWT_SECRET_KEY        # Generate: openssl rand -hex 32
SUPABASE_URL          # https://[project].supabase.co
SUPABASE_SERVICE_ROLE_KEY  # From Supabase dashboard
```

### 11.3 Backend CI/CD (FastAPI → Render)

Create `.github/workflows/backend.yml`:

```yaml
name: Backend CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  PYTHON_VERSION: '3.14'

jobs:
  # ============================================
  # JOB 1: Lint and Type Check
  # ============================================
  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install ruff mypy
          pip install -r requirements.txt

      - name: Run Ruff linter
        run: ruff check .

      - name: Run MyPy type check
        run: mypy . --ignore-missing-imports

  # ============================================
  # JOB 2: Run Tests
  # ============================================
  test:
    name: Run Tests
    runs-on: ubuntu-latest
    needs: lint
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql://test:test@localhost:5432/test
      JWT_SECRET_KEY: test-secret-key-for-ci
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-cov pytest-asyncio httpx

      - name: Run tests with coverage
        run: pytest --cov=. --cov-report=xml -v

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage.xml
          fail_ci_if_error: false

  # ============================================
  # JOB 3: Deploy to Render (only on main)
  # ============================================
  deploy:
    name: Deploy to Render
    runs-on: ubuntu-latest
    needs: [lint, test]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    steps:
      - name: Trigger Render Deploy
        run: |
          curl -X POST \
            -H "Authorization: Bearer ${{ secrets.RENDER_API_KEY }}" \
            -H "Content-Type: application/json" \
            "https://api.render.com/v1/services/${{ secrets.RENDER_SERVICE_ID }}/deploys"
```

### 11.4 Backend Dockerfile

Create `Dockerfile` in backend root:

```dockerfile
FROM python:3.14-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN adduser --disabled-password --gecos '' appuser
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 11.5 Render Configuration

Create `render.yaml` in backend root:

```yaml
services:
  - type: web
    name: maichienglish-api
    env: python
    region: singapore  # or nearest to Vietnam
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: JWT_SECRET_KEY
        sync: false
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_SERVICE_ROLE_KEY
        sync: false
```

### 11.6 Branch Workflow

```
main (production)
 │
 └── develop (staging)
      │
      ├── feature/auth-endpoints
      ├── feature/exam-crud
      ├── fix/login-error
      └── chore/update-deps
```

**Workflow:**
1. Create feature branch from `develop`
2. Push commits, CI runs lint + tests
3. Open PR to `develop`
4. Code review + merge
5. `develop` deploys to staging (optional)
6. When ready, PR from `develop` to `main`
7. `main` deploys to production

### 11.7 Health Check Endpoint

Add this to `main.py`:

```python
@app.get("/health")
async def health_check():
    """Health check endpoint for deployment platforms."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "service": "maichienglish-api",
    }
```

---

## Open Questions for Architect Review

The following items remain open for the backend and require an architect decision:

1. **Session Strategy**: Pure JWT or add Redis for token blacklisting?
2. **Rate Limiting**: Add API rate limiting for free tier?

> Resolved (kept for traceability):
> - **Database**: New Supabase project, PostgreSQL 17, schema from scratch ✅
> - **Storage**: Supabase Storage, same project ✅
> - **Deployment**: Render (Singapore) ✅

---

## Contact

For questions about this document, contact the project owner.
