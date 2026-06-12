---
feature: session-management
status: approved        # design chốt, chưa implement
type: design
last-updated: 2026-06-02
author: Backend team
depends-on: [auth, attempts]
---

# Session Management — Thiết kế & Core feature

> **Scope**: hai vấn đề v1 chưa xử lý, gộp trong một feature vì cùng mặt "ngừng
> giả định phiên kéo dài mãi":
> - **Part A — Session inactivity**: user đăng nhập idle > 30 phút → buộc đăng nhập lại.
> - **Part B — Exam duration enforcement**: attempt để mở quá `exam.duration_minutes`
>   → tự abandon ở lần tương tác kế tiếp.
>
> Đóng lại giới hạn ghi ở [`attempt-lifecycle design §6.4`](../attempt-lifecycle/attempt-lifecycle-design.md#64-session-timeout).
> Bổ sung cho [`attempt-lifecycle design`](../attempt-lifecycle/attempt-lifecycle-design.md) (state machine) và
> `config/settings.py` (JWT expiry).
>
> Tài liệu cùng feature: [Frontend notes](session-management-frontend.md) ·
> [Test cases](session-management-testcases.md). Chi tiết API xem **Swagger**.

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [Current state — gaps](#3-current-state--gaps)
4. [Part A — Session inactivity (30 phút)](#4-part-a--session-inactivity-30-phút)
5. [Part B — Exam duration enforcement](#5-part-b--exam-duration-enforcement)
6. [Cố ý KHÔNG làm](#6-cố-ý-không-làm)
7. [API changes summary](#7-api-changes-summary)
8. [Future v2 roadmap](#8-future-v2-roadmap)
9. [Appendix: files to change](#9-appendix-files-to-change)

---

## 1. Overview

Hai vấn đề thường bị gộp nhầm thành "session timeout" nhưng **cơ chế khác nhau**:

| Problem | Ngưỡng | Cơ chế |
|---|---|---|
| **A. Session inactivity** | 30 phút không có API activity | Rút JWT access token còn 30 phút; FE refresh chủ động khi user active; idle ≥30 phút → token hết hạn → BE trả 401 → FE buộc đăng nhập lại |
| **B. Exam duration overrun** | `exam.duration_minutes` (đã có trong DB) | **Lazy enforcement** — ở mọi API chạm attempt, BE so `now() - started_at` với `duration_minutes`; nếu quá hạn & còn `in_progress` → tự abandon atomically trong cùng transaction |

```
Case A — session idle                 Case B — exam deadline expired
┌──────────────────────┐              ┌──────────────────────────────┐
│ Đăng nhập 09:00      │              │ Bắt đầu thi 09:00            │
│ Hoạt động liên tục   │              │ duration_minutes = 60        │
│ tới 10:00            │              │ Đóng máy 09:30               │
│ Idle từ 10:00        │              │ ── deadline = 10:00 ──       │
│ ── 10:30 ──          │              │ Mở lại 14:00 → PATCH/answers │
│ JWT hết hạn          │              │ BE: 14:00 > 10:00            │
│ FE → 401 → re-login  │              │ → auto-abandon + 400         │
└──────────────────────┘              └──────────────────────────────┘
```

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | 2 vấn đề, 2 cơ chế | **Tách rõ idle (JWT) vs duration (wall-clock từ `started_at`)** | Gộp làm một "timeout" | Ngưỡng + semantics khác nhau; gộp sẽ sai. |
| 2 | Cơ chế session idle | **Rút access token 30 phút + FE refresh chủ động** | Cột `last_active_at` (ghi DB mỗi request, cần migration); FE-only idle detect | Server-enforced; không migration; chống client gian lận vì exp đã ký. |
| 3 | Nguồn thời gian | **Server `now()` là chuẩn (UTC)** | Tin client clock | Không bao giờ tin đồng hồ client. |
| 4 | Enforcement duration | **Lazy (inline trên API kế tiếp)** | Cron/background worker | Không cần infra; data vẫn đúng (`started_at + duration` là chuẩn); UI gated bởi GET /active. Cái giá: attempt quá hạn vẫn `in_progress` trong DB tới khi có call. |
| 5 | Khi hết giờ | **Auto-abandon (score=0), KHÔNG auto-submit** | Auto-submit đáp án dở | Quyết định của user: rời phòng trước chuông ≠ "nộp cái đang viết". Đáp án đã lưu giữ lại để audit nhưng `is_correct=NULL`, không chấm. |
| 6 | `GET /active` khi expired | **Lazy-write auto-abandon → trả 404** | Trả payload kèm `isExpired:true`, không abandon | FE một mental model "GET /active là sự thật"; đơn giản hơn. Đánh đổi: GET gây side-effect nhỏ (1 UPDATE, idempotent). |
| 7 | Schema | **Không cột mới / không migration** | Thêm cột tracking | `exam.duration_minutes` + `attempt.started_at` + JWT expiry là đủ. |
| 8 | Refresh-token rotation | **Giữ nguyên (reuse refresh token) ở v1** | Rotate mỗi lần /refresh | 7-day TTL + xoay `JWT_SECRET_KEY` đủ an toàn v1. Rotation → v2 hardening. |

---

## 3. Current state — gaps

### 3.1 Session inactivity

| Có sẵn | Thiếu |
|---|---|
| Access token hết hạn sau 60 phút (default) | Không reset theo inactivity — hết hạn tuyệt đối, không phải "sau 60 phút không dùng" |
| Refresh token 7 ngày | FE renew vô hạn; "phiên" thực tế không bao giờ kết thúc tới khi refresh hết hạn |
| `POST /api/auth/refresh` | Không throttle |
| — | Không track `last_active_at` ở đâu cả |

### 3.2 Exam duration

| Có sẵn | Thiếu |
|---|---|
| Cột `exam.duration_minutes` | Không code nào đọc để enforce (chỉ hiển thị) |
| Cột `attempt.started_at` | Không code nào tính deadline `started_at + duration` |
| `attempt.is_abandoned` + state machine | Không trigger theo thời gian |
| `POST /api/attempts/{id}/abandon` thủ công | Không đường auto-abandon |

> Doc này **thay** ghi chú "không auto-expiry" ở ATTEMPT_LIFECYCLE §5.4 cho trường hợp
> *duration overrun* (thời gian từ `started_at`). Idle-based auto-abandon là cousin
> nghiêm hơn → v2 ([§8](#8-future-v2-roadmap)).

---

## 4. Part A — Session inactivity (30 phút)

### 4.1 Cách chọn: short access token + proactive refresh

Ba phương án (xem [§2](#2-design-decisions--trade-offs) #2): chọn **rút access token còn
30 phút; FE refresh chủ động khi user active**.

### 4.2 Config change

```python
# config/settings.py — chỉ diff:
jwt_access_exp_minutes: int = Field(default=30, alias="JWT_ACCESS_EXP_MINUTES")
#  default 60 → 30
```

Refresh token (`jwt_refresh_exp_days=7`) giữ nguyên. `.env` override được.

### 4.3 Vì sao implement đúng "30 phút idle = buộc re-login"

```
Đăng nhập 09:00: access exp=09:30, refresh exp=+7 ngày
Case W — active liên tục:
  09:25 act → FE thấy token sắp hết (còn 5') → gọi /refresh → access exp=09:55 → tiếp...
  → kéo dài vô hạn miễn user còn act
Case I — idle từ 09:00:
  không act 09:00–09:30 → 09:30+ request bất kỳ → 401 → FE redirect login
```

Ngưỡng "30 phút idle" = "30 phút không refresh", chỉ xảy ra khi user active. Active
user được gia hạn liền mạch; idle user đụng tường.

### 4.4 FE responsibility

FE **phải refresh trước khi hết hạn khi user còn active** (không lazy đợi 401). Chi
tiết hành vi: xem [Frontend notes](session-management-frontend.md). Nếu FE không
refresh, token hết hạn và user thấy màn login ở hành động kế tiếp.

### 4.5 Refresh-token rotation (optional v1)

`POST /api/auth/refresh` hiện trả access token mới nhưng **dùng lại refresh token cũ**
— chấp nhận v1 ([§2](#2-design-decisions--trade-offs) #8). Rotation → v2 ([§8.1](#8-future-v2-roadmap)).

### 4.6 No new endpoint, no migration

Part A là **đổi config + đổi hành vi FE** thôi. Code BE không đổi.

---

## 5. Part B — Exam duration enforcement

### 5.1 Công thức deadline

```python
deadline = attempt.started_at + timedelta(minutes=exam.duration_minutes)
is_expired = (datetime.now(timezone.utc) > deadline) and attempt.is_active()
#  is_active = submitted_at IS NULL AND NOT is_abandoned
```

Mọi phép tính **UTC** (`started_at` là `timestamptz`). Nếu `duration_minutes` NULL
(không giới hạn) → không bao giờ expire.

### 5.2 Lazy enforcement (no cron)

Check chạy **inline trên mọi route chạm active attempt**:

```python
# services/attempt_service.py — helper mới
async def _check_expiry_and_maybe_abandon(conn, attempt: dict, exam: dict) -> bool:
    """True nếu attempt vừa bị auto-abandon do quá giờ. Idempotent."""
    if attempt["submitted_at"] is not None or attempt["is_abandoned"]:
        return False
    if exam["duration_minutes"] is None:
        return False
    deadline = attempt["started_at"] + timedelta(minutes=exam["duration_minutes"])
    if datetime.now(timezone.utc) <= deadline:
        return False
    await conn.execute(
        """
        UPDATE public.attempts
        SET is_abandoned = true, submitted_at = now(),
            score = 0, total_points = 0, percentage = 0
        WHERE id = $1 AND submitted_at IS NULL AND NOT is_abandoned
        """,
        attempt["id"],
    )
    return True
```

Guard `WHERE submitted_at IS NULL AND NOT is_abandoned` làm UPDATE race-safe — 2 call
đồng thời cùng thấy "expired" đều fire UPDATE nhưng chỉ 1 cập nhật được, cái kia no-op.

### 5.3 Route nào check expiry & phản hồi ra sao

| Route | Phản hồi khi expired |
|-------|----------------------|
| `POST /api/attempts` (start) — Case B (active cùng exam) | Auto-abandon old; tạo attempt mới; trả **201 Case A** thay vì 200 Case B. Quota +1. |
| `POST /api/attempts` (start) — Case C (active khác exam) | Auto-abandon attempt expired đó; tạo mới; trả **201 Case A**. 409 chỉ khi attempt conflicting còn trong giờ. |
| `PATCH /api/attempts/{id}/answers` | Auto-abandon; **400 `Exam time has expired; attempt has been finalized`**. Save bị từ chối — không ghi partial. |
| `POST /api/attempts/{id}/submit` | Auto-abandon; **400 `Exam time has expired; attempt has been finalized as abandoned (score=0)`**. Không chấm. |
| `POST /api/attempts/{id}/abandon` | Idempotent; nếu đã auto-abandon → 400 `Attempt already abandoned`. |
| `POST /api/attempts/{id}/sections/{sid}/audio-play` | Auto-abandon; **400 `Exam time has expired`**. |
| `GET /api/attempts/active` | **Lazy-write** auto-abandon → trả **404** (no active). |
| `GET /api/attempts/{id}` (detail) | Auto-abandon (lazy-write); trả detail với `isAbandoned=true`. |
| `GET /api/attempts/history` | Read-only; attempt expired-chưa-ghi hiện `in_progress` tới lần tương tác đầu. Chấp nhận v1 ([§8.3](#8-future-v2-roadmap)). |

### 5.4 `GET /active` khi expired

v1 chọn **lazy-write auto-abandon + trả 404** (xem [§2](#2-design-decisions--trade-offs) #6).

### 5.5 Auto-abandon ≠ auto-submit

Đáp án đã lưu **giữ lại trên row abandoned** để audit, nhưng attempt **abandoned, không
submit** — học sinh không được điểm phần làm dở khi bỏ đi. Lần vào sau thấy slate mới
qua resume dialog ("Bài cũ đã hết giờ, bắt đầu lại?" vì GET /active trả 404).

### 5.6 Tương tác "1 active globally"

Sau auto-abandon, partial unique index `attempts_one_active_per_user` không còn cover
row cũ → student bắt đầu attempt mới ngay. Race-safe nhờ index sẵn có.

### 5.7 Tương tác quota

Auto-abandoned attempt **tính vào quota** (như abandon thủ công, per
[`attempt-lifecycle design §6.1`](../attempt-lifecycle/attempt-lifecycle-design.md#61-quota)). Anti-abuse giữ nguyên.

### 5.8 Concurrency / race

| Race | Kết quả |
|---|---|
| `/answers` và `/submit` ở biên deadline | Call 1: chưa expired → OK. Call 2: expired → auto-abandon + 400. |
| 2 call đồng thời ngay sau deadline | Cả hai fire UPDATE với guard; 1 ghi, 1 no-op; cả hai trả 400. Không double-finalize. |
| `/abandon` + auto-abandon đồng thời | Guard → đúng 1 row chuyển; cái kia 400 `Attempt already abandoned`. |
| Clock drift | Server authoritative; client không override. Render NTP-synced. |

---

## 6. Cố ý KHÔNG làm

| Không làm | Vì sao |
|---|---|
| Cron/background sweep | Lazy đủ cho correctness. Sweep là tối ưu tương lai cho analytics/history ([§8.3](#8-future-v2-roadmap)). |
| Auto-submit thay auto-abandon | Đã loại ([§5.5](#55-auto-abandon--auto-submit)). |
| Per-question time tracking | Ngoài scope. KET/PET không tính giờ từng câu. |
| Idle-based attempt timeout | Nhìn passage 25 phút là *đọc*, không phải bỏ. Chỉ `duration_minutes` từ `started_at` trigger. v2 có thể thêm idle-warning UX. |
| Soft warning trước expiry | FE optional; BE không cần gửi "còn 5 phút". FE tự tính từ `startedAt + durationMinutes`. |
| Refresh-token rotation | v2 ([§8.1](#8-future-v2-roadmap)). |
| Cột `last_active_at` | Loại ([§2](#2-design-decisions--trade-offs) #2). |
| Session table (server-side) | Giữ stateless. v2 có thể thêm revocation list. |

---

## 7. API changes summary

### 7.1 Không endpoint mới

Tất cả là **hành vi nội bộ** của endpoint sẵn có.

### 7.2 Response thay đổi

| Endpoint | Hành vi mới |
|---|---|
| `POST /api/attempts` | Nếu attempt conflicting expired → auto-abandon rồi tiếp. Shape payload không đổi. |
| `PATCH /api/attempts/{id}/answers` | Lỗi mới: 400 `Exam time has expired; attempt has been finalized` |
| `POST /api/attempts/{id}/submit` | Lỗi mới: 400 `... finalized as abandoned (score=0)` |
| `POST /api/attempts/{id}/sections/{sid}/audio-play` | Lỗi mới: 400 `Exam time has expired` |
| `GET /api/attempts/active` | Lazy-write auto-abandon; trả 404 khi deadline qua |
| `GET /api/attempts/{id}` | Lazy-write auto-abandon; trả detail `isAbandoned=true` |

### 7.3 Response khi phiên 30 phút hết

JWT validation sẵn có đã trả **401** trên token hết hạn — không đổi. FE vốn đã chịu
trách nhiệm catch 401 → redirect login.

---

## 8. Future v2 roadmap

- **8.1 Refresh-token rotation + reuse detection** — mỗi /refresh trả refresh token mới; cũ bị vô hiệu (denylist). Reuse token cũ → nuke mọi session của user. Cần bảng `refresh_token_log` + `jti` claim.
- **8.2 Attempt idle auto-abandon** — ngoài duration overrun, thêm idle abandon (N giờ không save/audio-play) dù `duration_minutes` NULL. Cần cron (lazy không phát hiện được idle).
- **8.3 History display of not-yet-swept expired** — cron đêm quét "expired nhưng còn in_progress" cho dashboard/analytics.
- **8.4 Pre-expiry warning push** — SSE/polling "còn 5 phút". v1 FE tự tính local.
- **8.5 Per-skill duration limits** — `section.duration_minutes` với deadline + auto-abandon từng section. Nhiều việc FE; deferred.
- **8.6 Server-side session table (revocation)** — cho "logout all devices"/"admin force-logout". Stateless JWT không làm được. v2 nếu compliance cần.

---

## 9. Appendix: files to change

### 9.1 Config

| File | Change |
|------|---------|
| [config/settings.py](../../config/settings.py) | `jwt_access_exp_minutes` default 60 → 30 |
| `.env.example` | Ghi chú `JWT_ACCESS_EXP_MINUTES=30` (hoặc bỏ; default áp dụng) |

### 9.2 Service layer

| File | Change |
|------|---------|
| [services/attempt_service.py](../../services/attempt_service.py) | Thêm `_check_expiry_and_maybe_abandon(conn, attempt, exam)`; gọi từ `start_attempt` (trước quyết định Case B), `save_answers`, `submit_attempt`, `record_audio_play`, `get_active_attempt`, `get_attempt_with_answers` |

### 9.3 Route layer

| File | Change |
|------|---------|
| [api/attempts/routes.py](../../api/attempts/routes.py) | `ValidationError("Exam time has expired ...")` → 400 (handler hiện có đã cover `ValidationError`). Không cần error class mới. |

### 9.4 Schemas

Không đổi. `AttemptView.isAbandoned` đã có; auto-abandoned hiện ra tự nhiên với `isAbandoned=true`.

### 9.5 No migration / No new env vars

`exam.duration_minutes`, `attempts.started_at`, `attempts.is_abandoned` đều đã có.
`JWT_ACCESS_EXP_MINUTES` chỉ đổi default.

---

## Quick reference card

| Setting | Cũ | Mới (v1) | Vì sao |
|---|---|---|---|
| `JWT_ACCESS_EXP_MINUTES` | 60 | **30** | Buộc re-login sau 30 phút idle |
| `JWT_REFRESH_EXP_DAYS` | 7 | 7 (giữ) | Dài nhưng có giới hạn |
| Attempt deadline | none | `started_at + exam.duration_minutes` | Per-exam, server-authoritative |
| Khi hết giờ | n/a | **auto-abandon (score=0)** | Giữ đáp án để audit; không chấm |
| Enforcement | n/a | **Lazy (API kế tiếp)** | Không cron v1 |
| `GET /active` khi expired | n/a | **Lazy-write abandon + 404** | FE mental model đơn giản |
