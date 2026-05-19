# Mai Chi English - System Refactoring Plan

## Document Information

| Field | Value |
|-------|-------|
| **Project** | Mai Chi English Center - Online Testing System |
| **Document Type** | Requirements + High-Level Design (HLD) |
| **Version** | 1.0 |
| **Date** | May 2026 |
| **Target Audience** | Backend Developer, Frontend Developer, Architect Advisor |

---

## Executive Summary

This document outlines the plan to refactor the Mai Chi English application from a monolithic Next.js + Supabase architecture into a decoupled **Frontend (Next.js)** + **Backend (FastAPI)** architecture with independent JWT-based authentication and subscription management.

### Current State
- **Architecture**: Monolithic Next.js 14 with Server Actions
- **Database**: Supabase (PostgreSQL)
- **Auth**: Supabase Auth (session-based)
- **Roles**: `student`, `admin`

### Target State
- **Frontend**: Next.js 14 (SPA-like, API calls to backend)
- **Backend**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL (Supabase or standalone)
- **Auth**: Custom JWT tokens (access + refresh)
- **Roles**: `student`, `teacher`, `admin` (Ultra-equivalent)
- **Subscriptions**: Free, Basic, Pro, Ultra (Admin)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Backend Requirements](#2-backend-requirements)
3. [Frontend Requirements](#3-frontend-requirements)
4. [Database Schema](#4-database-schema)
5. [API Specification](#5-api-specification)
6. [Authentication & Authorization](#6-authentication--authorization)
7. [Subscription Tiers](#7-subscription-tiers)
8. [Implementation Phases](#8-implementation-phases)
9. [File Structure](#9-file-structure)
10. [Migration Checklist](#10-migration-checklist)
11. [Coding Standards and Patterns](#11-coding-standards-and-patterns) ⭐ NEW
12. [CI/CD Pipeline Setup](#12-cicd-pipeline-setup) ⭐ NEW

---

## 1. Architecture Overview

### Current Architecture (Before)

```
┌─────────────────────────────────────────────────────────┐
│                    Next.js 14 App                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Pages/UI   │  │Server Actions│  │  Middleware  │   │
│  │  (React)     │  │  (Backend)   │  │  (Auth)      │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │           │
│         └─────────────────┼─────────────────┘           │
│                           │                             │
└───────────────────────────┼─────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   Supabase    │
                    │ (Auth + DB +  │
                    │   Storage)    │
                    └───────────────┘
```

### Target Architecture (After)

```
┌──────────────────────────┐         ┌──────────────────────────┐
│      Next.js Frontend    │         │     FastAPI Backend      │
│                          │  HTTP   │                          │
│  ┌────────────────────┐  │  REST   │  ┌────────────────────┐  │
│  │  Pages (React)     │  │ ◄─────► │  │  API Routes        │  │
│  │  - Login/Signup    │  │   JWT   │  │  - /api/auth/*     │  │
│  │  - Dashboard       │  │         │  │  - /api/exams/*    │  │
│  │  - Exams           │  │         │  │  - /api/users/*    │  │
│  │  - Admin Panel     │  │         │  │  - /api/admin/*    │  │
│  └────────────────────┘  │         │  └─────────┬──────────┘  │
│                          │         │            │             │
│  ┌────────────────────┐  │         │  ┌─────────▼──────────┐  │
│  │  API Client        │  │         │  │  Services Layer    │  │
│  │  (fetch/axios)     │  │         │  │  - auth_service    │  │
│  │                    │  │         │  │  - exam_service    │  │
│  │  Token Storage:    │  │         │  │  - user_service    │  │
│  │  - localStorage    │  │         │  │  - subscription_svc│  │
│  │  - httpOnly cookie │  │         │  └─────────┬──────────┘  │
│  └────────────────────┘  │         │            │             │
│                          │         │            ▼             │
│  Port: 3000              │         │  ┌────────────────────┐  │
└──────────────────────────┘         │  │   PostgreSQL DB    │  │
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

### Key Changes

| Aspect | Before | After |
|--------|--------|-------|
| Backend | Server Actions (Next.js) | FastAPI (Python) |
| Auth | Supabase Auth (cookies) | Custom JWT (Bearer tokens) |
| API Style | Direct function calls | REST API endpoints |
| Deployment | Single Vercel app | Frontend (Vercel) + Backend (Docker/ECS) |
| Scalability | Limited | Horizontal scaling possible |

---

## 2. Backend Requirements

### 2.1 Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Framework | FastAPI | 0.110+ |
| Python | Python | 3.11+ |
| Database | PostgreSQL (via Supabase or standalone) | 14+ |
| ORM | SQLAlchemy 2.0 or asyncpg | Latest |
| Auth | PyJWT + bcrypt | Latest |
| Validation | Pydantic v2 | 2.0+ |
| ASGI Server | Uvicorn | Latest |

### 2.2 Required API Modules

```
maichienglish_api/
├── api/
│   ├── auth/           # Authentication endpoints
│   ├── exams/          # Exam CRUD + publishing
│   ├── questions/      # Question management
│   ├── attempts/       # Student exam attempts
│   ├── users/          # User profile management
│   ├── admin/          # Admin-only operations
│   └── subscriptions/  # Subscription management
├── services/           # Business logic layer
├── models/             # SQLAlchemy models
├── schemas/            # Pydantic schemas
├── utils/              # JWT, password hashing, etc.
└── config/             # Settings, database config
```

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
- [x] Password reset by admin
- [x] User profile CRUD
- [x] Role-based access (student, teacher, admin)

#### Subscription Management (NEW)
- [x] Subscription tiers: Free, Basic, Pro, Ultra
- [x] Feature flags per tier
- [x] Credit system (for future AI features)
- [x] Admin (Ultra) can manage all subscriptions

#### Exam Management
- [x] CRUD for exams
- [x] Publish/unpublish toggle
- [x] Question CRUD (4 types: multiple_choice, fill_blank, matching, image_choice)
- [x] Excel import for questions
- [x] Audio file management

#### Attempt Management
- [x] Start exam attempt
- [x] Submit answers
- [x] Auto-grading
- [x] View results
- [x] History tracking

---

## 3. Frontend Requirements

### 3.1 Changes from Current Implementation

| Component | Current | After Refactoring |
|-----------|---------|-------------------|
| Auth | Supabase client SDK | Custom API calls + token storage |
| Data Fetching | Server Actions | `fetch()` or axios to REST API |
| Middleware | Check Supabase session | Check JWT in localStorage/cookie |
| Forms | Direct server action calls | API POST/PUT requests |

### 3.2 New Frontend Structure

```
maichienglish/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx         # Login form → POST /api/auth/login
│   │   └── forgot-password/       # Password reset flow
│   ├── (dashboard)/
│   │   ├── dashboard/page.tsx     # Student dashboard
│   │   ├── exams/                 # Exam list + taking exams
│   │   ├── history/               # Attempt history
│   │   └── admin/                 # Admin panel
│   └── layout.tsx
├── lib/
│   ├── api-client.ts              # NEW: Axios/fetch wrapper
│   ├── auth-context.tsx           # NEW: Auth state management
│   └── hooks/                     # NEW: useAuth, useApi hooks
├── middleware.ts                   # Updated: Check JWT token
└── components/
```

### 3.3 API Client Implementation

```typescript
// lib/api-client.ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

class ApiClient {
  private getToken(): string | null {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('accessToken');
    }
    return null;
  }

  async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const token = this.getToken();
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (response.status === 401) {
      // Try to refresh token
      const refreshed = await this.refreshToken();
      if (refreshed) {
        return this.request(endpoint, options);
      }
      // Redirect to login
      window.location.href = '/login';
      throw new Error('Unauthorized');
    }

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'API Error');
    }

    return response.json();
  }

  async refreshToken(): Promise<boolean> {
    const refreshToken = localStorage.getItem('refreshToken');
    if (!refreshToken) return false;

    try {
      const response = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken }),
      });

      if (response.ok) {
        const data = await response.json();
        localStorage.setItem('accessToken', data.data.token.accessToken);
        localStorage.setItem('refreshToken', data.data.token.refreshToken);
        return true;
      }
    } catch (e) {
      console.error('Token refresh failed', e);
    }
    return false;
  }

  // Auth methods
  async login(email: string, password: string) {
    return this.request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
  }

  async logout() {
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('user');
  }

  // Exam methods
  async getExams() {
    return this.request('/api/exams');
  }

  async getExam(id: string) {
    return this.request(`/api/exams/${id}`);
  }

  // ... other methods
}

export const api = new ApiClient();
```

---

## 4. Database Schema

### 4.1 Tables (Keep existing + add subscriptions)

```sql
-- ============================================================
-- EXISTING TABLES (Keep as-is from Supabase)
-- ============================================================

-- profiles: User profiles (linked to auth)
-- exams: Exam definitions
-- questions: Exam questions
-- attempts: Student exam attempts
-- answers: Student answers per attempt

-- ============================================================
-- NEW: Subscriptions Table
-- ============================================================

CREATE TABLE IF NOT EXISTS public.subscriptions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  tier          text NOT NULL DEFAULT 'free'
                CHECK (tier IN ('free', 'basic', 'pro', 'ultra')),
  status        text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'canceled', 'expired')),
  credits_monthly    int NOT NULL DEFAULT 0,
  credits_remaining  int NOT NULL DEFAULT 0,
  current_period_start timestamptz NOT NULL DEFAULT now(),
  current_period_end   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),

  UNIQUE(user_id)
);

-- ============================================================
-- NEW: Password Hash Column (if not using Supabase Auth)
-- ============================================================

-- Option A: Add password_hash to profiles
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS password_hash text;

-- Option B: Create separate users table for auth
CREATE TABLE IF NOT EXISTS public.users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  is_verified   boolean NOT NULL DEFAULT true,  -- Admin creates accounts, no email verify
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Update profiles to reference users
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES public.users(id) ON DELETE CASCADE;
```

### 4.2 Migration Strategy

**Option A: Keep Supabase DB, Replace Auth Only**
- Keep PostgreSQL tables in Supabase
- Replace Supabase Auth with custom JWT auth
- Add `password_hash` column to profiles
- Backend connects directly to Supabase PostgreSQL

**Option B: Full Independence (Recommended for scale)**
- Migrate to standalone PostgreSQL (or keep Supabase PostgreSQL)
- Create new `users` table for auth
- Custom JWT auth in FastAPI
- Supabase Storage for files only

---

## 5. API Specification

### 5.1 Authentication Endpoints

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

#### POST /api/auth/password/request-code
Request password reset code.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response (200):**
```json
{
  "message": "Code sent",
  "expiresIn": 600
}
```

#### POST /api/auth/password/reset
Reset password with code.

**Request:**
```json
{
  "email": "user@example.com",
  "code": "123456",
  "newPassword": "newPassword123"
}
```

### 5.2 User Endpoints

#### GET /api/users/me
Get current user profile.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
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
```

### 5.3 Admin Endpoints

#### POST /api/admin/users
Create new user (admin only).

**Request:**
```json
{
  "email": "student@example.com",
  "password": "tempPassword123",
  "fullName": "Nguyen Van B",
  "role": "student",
  "phone": "0909876543",
  "subscriptionTier": "basic"
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

### 5.4 Exam Endpoints

#### GET /api/exams
List exams (filtered by role: students see published only).

**Query Params:**
- `level`: Filter by level (primary, secondary, KET, PET, IELTS)
- `skill`: Filter by skill (listening, reading)
- `published`: true/false (admin only)

#### POST /api/exams (admin only)
Create new exam.

#### PUT /api/exams/{exam_id} (admin only)
Update exam.

#### POST /api/exams/{exam_id}/publish (admin only)
Publish exam (requires at least 1 question).

#### DELETE /api/exams/{exam_id} (admin only)
Delete exam.

### 5.5 Question Endpoints

#### GET /api/exams/{exam_id}/questions
Get questions for an exam.

#### POST /api/exams/{exam_id}/questions (admin only)
Add question to exam.

#### PUT /api/questions/{question_id} (admin only)
Update question.

#### DELETE /api/questions/{question_id} (admin only)
Delete question.

#### POST /api/exams/{exam_id}/questions/import (admin only)
Import questions from Excel.

### 5.6 Attempt Endpoints

#### POST /api/attempts
Start new exam attempt.

**Request:**
```json
{
  "examId": "uuid"
}
```

**Response:**
```json
{
  "attemptId": "uuid",
  "exam": { ... },
  "questions": [ ... ],
  "startedAt": "2026-05-12T10:00:00Z"
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

**Response:**
```json
{
  "score": 8.5,
  "totalPoints": 10,
  "percentage": 85,
  "submittedAt": "2026-05-12T10:30:00Z"
}
```

#### GET /api/attempts/{attempt_id}
Get attempt details with answers.

#### GET /api/attempts/history
Get student's attempt history.

### 5.7 Subscription Endpoints

#### GET /api/subscriptions/me
Get current user's subscription.

#### GET /api/subscriptions/plans
Get available plans and features.

#### PUT /api/admin/subscriptions/{user_id} (admin only)
Update user subscription tier.

---

## 6. Authentication & Authorization

### 6.1 JWT Token Structure

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

### 6.2 Password Hashing

Use **bcrypt** with cost factor 12 (matching cloudmortgage pattern):

```python
import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
```

### 6.3 Role-Based Access Control

| Role | Permissions |
|------|-------------|
| `student` | View published exams, take exams, view own results |
| `teacher` | All student permissions + view all results, export data |
| `admin` | All permissions (Ultra tier), manage users, subscriptions |

### 6.4 FastAPI Dependencies

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

## 7. Subscription Tiers

### 7.1 Tier Definitions

| Tier | Price | Target User | Key Features |
|------|-------|-------------|--------------|
| **Free** | $0 | Trial students | 5 attempts/month, basic reports |
| **Basic** | $9.99/mo | Regular students | 50 attempts/month, full history |
| **Pro** | $19.99/mo | Power users | Unlimited attempts, analytics |
| **Ultra** | $0 (Admin) | Teachers/Admins | All features, user management |

### 7.2 Feature Matrix

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

### 7.3 Subscription Plan Implementation

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

## 8. Implementation Phases

### Phase 1: Backend Foundation (Week 1-2)

**Backend Developer Tasks:**

1. **Project Setup**
   - [ ] Create FastAPI project structure
   - [ ] Configure Pydantic settings (DB URL, JWT secret, etc.)
   - [ ] Set up database connection (asyncpg or SQLAlchemy)
   - [ ] Configure CORS for frontend

2. **Authentication Module**
   - [ ] Implement JWT token generation (access + refresh)
   - [ ] Implement password hashing (bcrypt)
   - [ ] Create `/api/auth/login` endpoint
   - [ ] Create `/api/auth/refresh` endpoint
   - [ ] Create `/api/auth/password/*` endpoints
   - [ ] Create `get_current_user` dependency

3. **User Management**
   - [ ] Create `/api/users/me` endpoint
   - [ ] Create admin user creation endpoint
   - [ ] Implement role-based access control

**Frontend Developer Tasks:**

1. **API Client Setup**
   - [ ] Create `api-client.ts` with fetch wrapper
   - [ ] Implement token storage (localStorage)
   - [ ] Implement token refresh logic
   - [ ] Create auth context/provider

2. **Auth Pages Update**
   - [ ] Update login page to call API
   - [ ] Update forgot-password flow
   - [ ] Update middleware for JWT validation

### Phase 2: Core Features (Week 3-4)

**Backend Developer:**

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

**Frontend Developer:**

1. **Remove Server Actions**
   - [ ] Replace all Server Action calls with API calls
   - [ ] Update form submissions
   - [ ] Update data fetching

2. **Update Middleware**
   - [ ] Remove Supabase auth checks
   - [ ] Add JWT token validation
   - [ ] Update role-based redirects

### Phase 3: Subscription & Polish (Week 5-6)

**Backend Developer:**

1. **Subscription System**
   - [ ] Implement subscription plans
   - [ ] Create subscription endpoints
   - [ ] Add feature access checks
   - [ ] Implement attempt limits

2. **Testing & Documentation**
   - [ ] Write unit tests for auth
   - [ ] Write API documentation
   - [ ] Add request validation

**Frontend Developer:**

1. **Subscription UI**
   - [ ] Add subscription display
   - [ ] Show feature locks
   - [ ] Upgrade prompts

2. **Polish**
   - [ ] Error handling
   - [ ] Loading states
   - [ ] Mobile testing

---

## 9. File Structure

### Backend (FastAPI)

```
maichienglish_api/
├── main.py                         # FastAPI app entry point
├── requirements.txt
├── Dockerfile
├── .env.example
│
├── config/
│   ├── __init__.py
│   ├── settings.py                 # Pydantic settings (env vars)
│   └── database.py                 # Database connection
│
├── api/
│   ├── __init__.py
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── routes.py               # Auth endpoints
│   │   └── schemas.py              # Auth Pydantic models
│   ├── users/
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── admin/
│   │   ├── routes.py               # Admin-only endpoints
│   │   └── schemas.py
│   ├── exams/
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── questions/
│   │   ├── routes.py
│   │   └── schemas.py
│   ├── attempts/
│   │   ├── routes.py
│   │   └── schemas.py
│   └── subscriptions/
│       ├── routes.py
│       └── schemas.py
│
├── services/
│   ├── __init__.py
│   ├── auth_service.py             # Login, JWT, password reset
│   ├── user_service.py             # User CRUD
│   ├── exam_service.py             # Exam CRUD
│   ├── question_service.py
│   ├── attempt_service.py          # Attempts + grading
│   ├── subscription_service.py
│   └── subscription_plans.py       # Plan definitions
│
├── models/                          # SQLAlchemy models (if using ORM)
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
│   ├── grading_utils.py            # Grade fill_blank, matching
│   └── excel_utils.py              # Excel import
│
└── dependencies.py                  # FastAPI dependencies
```

### Frontend (Updated Next.js)

```
maichienglish/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx          # Updated: API calls
│   │   └── forgot-password/page.tsx
│   ├── (dashboard)/
│   │   ├── layout.tsx              # Updated: Auth check
│   │   ├── dashboard/page.tsx
│   │   ├── exams/
│   │   │   ├── page.tsx            # Exam list (API)
│   │   │   └── [id]/
│   │   │       ├── page.tsx        # Take exam
│   │   │       └── result/[attemptId]/page.tsx
│   │   ├── history/page.tsx
│   │   └── admin/
│   │       ├── page.tsx
│   │       ├── exams/
│   │       └── students/
│   └── layout.tsx
│
├── lib/
│   ├── api-client.ts               # NEW: REST API client
│   ├── auth-context.tsx            # NEW: Auth state provider
│   ├── hooks/
│   │   ├── useAuth.ts              # NEW: Auth hook
│   │   └── useApi.ts               # NEW: API hook
│   ├── utils.ts                    # Keep existing utils
│   └── supabase/                   # REMOVE or keep for storage only
│
├── components/
│   ├── ui/                         # Keep shadcn/ui
│   ├── layout/
│   │   └── Header.tsx              # Updated: API user
│   ├── admin/                      # Keep, update data fetching
│   └── student/                    # Keep, update data fetching
│
├── middleware.ts                   # Updated: JWT check
│
├── .env.local
│   # NEXT_PUBLIC_API_URL=http://localhost:8000
│   # NEXT_PUBLIC_SUPABASE_URL=... (for storage only)
│
└── app/actions/                    # REMOVE: No longer needed
```

---

## 10. Migration Checklist

### Pre-Migration

- [ ] Export all Supabase data (profiles, exams, questions, attempts, answers)
- [ ] Document all current RLS policies
- [ ] Note Supabase Storage bucket structure

### Backend Setup

- [ ] Create FastAPI project with structure above
- [ ] Configure PostgreSQL connection to Supabase
- [ ] Add `password_hash` column to profiles
- [ ] Generate password hashes for existing users (temporary passwords)
- [ ] Test auth endpoints with Postman

### Frontend Migration

- [ ] Create `lib/api-client.ts`
- [ ] Create `lib/auth-context.tsx`
- [ ] Update `middleware.ts` for JWT validation
- [ ] Replace Server Actions one by one:
  - [ ] `auth.ts` → API calls
  - [ ] `admin.ts` → API calls
  - [ ] `exams.ts` → API calls
  - [ ] `questions.ts` → API calls
- [ ] Test each feature after migration

### Post-Migration

- [ ] Remove `app/actions/` directory
- [ ] Remove unused Supabase client code (keep storage only)
- [ ] Update environment variables
- [ ] Deploy backend to Docker/ECS
- [ ] Deploy frontend to Vercel
- [ ] Update DNS/API routes if needed

---

## Environment Variables

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

### Frontend (.env.local)

```env
# API
NEXT_PUBLIC_API_URL=http://localhost:8000

# Supabase (storage only)
NEXT_PUBLIC_SUPABASE_URL=https://[PROJECT].supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

---

## 11. Coding Standards and Patterns

> **IMPORTANT**: All code MUST follow the patterns established in the cloudmortgage.io codebase. This ensures consistency, maintainability, and easier code reviews.

### 11.1 Settings Configuration Pattern

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

### 11.2 JWT Token Utilities Pattern

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

### 11.3 Service Class Pattern

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

### 11.4 API Routes Pattern

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

### 11.5 Pydantic Schemas Pattern

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

### 11.6 Dependencies Pattern

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

### 11.7 Logging Standards

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

### 11.8 Exception Handling Pattern

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

### 11.9 Database Query Pattern

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

### 11.10 API Response Format

All API responses should follow this consistent format:

```python
# Success response
{
    "status": 200,
    "data": {
        # Response data here
    }
}

# Error response (handled by FastAPI HTTPException)
{
    "detail": "Error message here"
}

# List response with pagination
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

### 11.11 File Organization Rules

1. **One service per domain**: `user_service.py`, `exam_service.py`, not `service.py`
2. **Schemas next to routes**: `api/auth/routes.py` and `api/auth/schemas.py`
3. **Utils are generic**: Password hashing, JWT, grading algorithms
4. **Services contain business logic**: No HTTP/FastAPI imports in services
5. **Config is separate**: All env vars in `config/settings.py`

### 11.12 Code Style Checklist

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

## 12. CI/CD Pipeline Setup

> **Note**: This setup uses Supabase for database and storage. No AWS migration required.

### 12.1 Repository Structure

**Recommended: Two Separate Repositories**

```
github.com/your-org/
├── maichienglish-frontend/    # Next.js app → Deploy to Vercel
└── maichienglish-api/         # FastAPI app → Deploy to Railway/Render/Fly.io
```

**Alternative: Monorepo (if preferred)**

```
github.com/your-org/maichienglish/
├── frontend/                  # Next.js app
├── backend/                   # FastAPI app
├── .github/
│   └── workflows/
│       ├── frontend.yml
│       └── backend.yml
└── README.md
```

### 12.2 GitHub Repository Setup

#### Step 1: Create Repositories

```bash
# Option A: Separate repos
gh repo create maichienglish-frontend --private --clone
gh repo create maichienglish-api --private --clone

# Option B: Monorepo
gh repo create maichienglish --private --clone
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

**Frontend Repository Secrets:**
```
VERCEL_TOKEN          # Get from Vercel dashboard
VERCEL_ORG_ID         # vercel.com/account
VERCEL_PROJECT_ID     # From .vercel/project.json after linking
```

**Backend Repository Secrets:**
```
# For Railway deployment
RAILWAY_TOKEN         # railway.app/account/tokens

# OR for Render deployment
RENDER_API_KEY        # render.com/docs/api
RENDER_SERVICE_ID     # From Render dashboard

# OR for Fly.io deployment
FLY_API_TOKEN         # fly.io/user/personal_access_tokens

# Application secrets (same for all platforms)
DATABASE_URL          # Your Supabase PostgreSQL connection string
JWT_SECRET_KEY        # Generate: openssl rand -hex 32
SUPABASE_URL          # https://[project].supabase.co
SUPABASE_SERVICE_ROLE_KEY  # From Supabase dashboard
```

### 12.3 Frontend CI/CD (Next.js → Vercel)

Create `.github/workflows/frontend.yml`:

```yaml
name: Frontend CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  NODE_VERSION: '20'

jobs:
  # ============================================
  # JOB 1: Lint and Type Check
  # ============================================
  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run ESLint
        run: npm run lint

      - name: Run TypeScript check
        run: npm run type-check

  # ============================================
  # JOB 2: Run Tests
  # ============================================
  test:
    name: Run Tests
    runs-on: ubuntu-latest
    needs: lint
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run tests
        run: npm test -- --coverage

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage/lcov.info
          fail_ci_if_error: false

  # ============================================
  # JOB 3: Deploy to Vercel (Preview on PR, Production on main)
  # ============================================
  deploy:
    name: Deploy to Vercel
    runs-on: ubuntu-latest
    needs: [lint, test]
    steps:
      - uses: actions/checkout@v4

      - name: Install Vercel CLI
        run: npm install -g vercel

      - name: Pull Vercel Environment
        run: vercel pull --yes --environment=${{ github.ref == 'refs/heads/main' && 'production' || 'preview' }} --token=${{ secrets.VERCEL_TOKEN }}

      - name: Build Project
        run: vercel build ${{ github.ref == 'refs/heads/main' && '--prod' || '' }} --token=${{ secrets.VERCEL_TOKEN }}

      - name: Deploy to Vercel
        id: deploy
        run: |
          url=$(vercel deploy --prebuilt ${{ github.ref == 'refs/heads/main' && '--prod' || '' }} --token=${{ secrets.VERCEL_TOKEN }})
          echo "url=$url" >> $GITHUB_OUTPUT

      - name: Comment PR with Preview URL
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: '🚀 Preview deployed: ${{ steps.deploy.outputs.url }}'
            })
```

**Required `package.json` scripts:**
```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "type-check": "tsc --noEmit",
    "test": "jest"
  }
}
```

### 12.4 Backend CI/CD (FastAPI → Railway)

Create `.github/workflows/backend.yml`:

```yaml
name: Backend CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  PYTHON_VERSION: '3.11'

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
        image: postgres:14
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
  # JOB 3: Deploy to Railway (only on main)
  # ============================================
  deploy:
    name: Deploy to Railway
    runs-on: ubuntu-latest
    needs: [lint, test]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    steps:
      - uses: actions/checkout@v4

      - name: Install Railway CLI
        run: npm install -g @railway/cli

      - name: Deploy to Railway
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: railway up --detach

      - name: Notify deployment success
        run: echo "✅ Deployed to Railway successfully"
```

### 12.5 Alternative: Deploy to Render

If using Render instead of Railway, replace the deploy job:

```yaml
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

### 12.6 Alternative: Deploy to Fly.io

If using Fly.io instead:

```yaml
  deploy:
    name: Deploy to Fly.io
    runs-on: ubuntu-latest
    needs: [lint, test]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    steps:
      - uses: actions/checkout@v4

      - uses: superfly/flyctl-actions/setup-flyctl@master

      - name: Deploy to Fly.io
        run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

### 12.7 Backend Dockerfile

Create `Dockerfile` in backend root:

```dockerfile
FROM python:3.11-slim

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

### 12.8 Railway Configuration

Create `railway.toml` in backend root:

```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 100
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3
```

### 12.9 Render Configuration

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

### 12.10 Fly.io Configuration

Create `fly.toml` in backend root:

```toml
app = "maichienglish-api"
primary_region = "sin"  # Singapore, nearest to Vietnam

[build]
  dockerfile = "Dockerfile"

[env]
  PORT = "8000"

[http_service]
  internal_port = 8000
  force_https = true
  auto_start_machines = true
  auto_stop_machines = true
  min_machines_running = 1

[[http_service.checks]]
  grace_period = "10s"
  interval = "30s"
  method = "GET"
  path = "/health"
  timeout = "5s"
```

### 12.11 Branch Workflow

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

### 12.12 Health Check Endpoint

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

### 12.13 Platform Comparison

| Feature | Railway | Render | Fly.io | Vercel |
|---------|---------|--------|--------|--------|
| **Best for** | Simple deploys | Full-featured | Edge/Global | Frontend |
| **Docker support** | ✅ | ✅ | ✅ | ❌ |
| **Free tier** | $5 credit | 750 hrs/mo | 3 VMs | Generous |
| **Auto-deploy** | ✅ | ✅ | ✅ | ✅ |
| **Region** | US, EU | US, EU, SG | Global | Global |
| **PostgreSQL** | ✅ | ✅ | ✅ (Supabase) | ❌ |
| **Price (basic)** | ~$5/mo | Free-$7/mo | ~$5/mo | Free |

**Recommendation for Vietnam-based app:**
- **Frontend**: Vercel (free, global CDN)
- **Backend**: Render or Fly.io with Singapore region for low latency

---

## Questions for Architect Review

1. **Database Strategy**: ~~Keep Supabase PostgreSQL or migrate to standalone?~~ → **Staying on Supabase** ✅
2. **File Storage**: Keep Supabase Storage or move to S3/CloudFlare R2?
3. **Deployment**: ~~Docker on VPS vs AWS ECS vs Railway/Render?~~ → **Railway, Render, or Fly.io** ✅
4. **Session Strategy**: Pure JWT or add Redis for token blacklisting?
5. **Rate Limiting**: Add API rate limiting for free tier?

---

## Contact

For questions about this document, contact the project owner.
