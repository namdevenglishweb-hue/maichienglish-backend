---
feature: email
type: design
status: approved        # design chốt, chưa implement
last-updated: 2026-06-02
author: Backend team
depends-on: [auth, admin-users, attempts]
---

# Email Integration — Thiết kế & Core feature

> **Scope**: backend gửi transactional email (password reset OTP, welcome khi
> admin tạo account, parent notification khi nộp bài) qua Gmail SMTP. Đóng lại
> luồng password-reset hiện đang stub bằng field `devCode`.
>
> Bổ sung cho [`MAICHIENGLISH_BACKEND_PLAN.md`](../../MAICHIENGLISH_BACKEND_PLAN.md)
> §5 (auth) và `services/auth_service.py` reset-code lifecycle.
>
> Tài liệu liên quan trong cùng feature: [Frontend notes](email-frontend.md) ·
> [Test cases](email-testcases.md). Chi tiết API xem **Swagger**.

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [Email types — v1 scope & v2 roadmap](#3-email-types--v1-scope--v2-roadmap)
4. [Backend architecture (adapter pattern)](#4-backend-architecture-adapter-pattern)
5. [Background sending model](#5-background-sending-model)
6. [Template strategy](#6-template-strategy)
7. [Gmail App Password setup](#7-gmail-app-password-setup)
8. [Password reset flow — thay devCode](#8-password-reset-flow--thay-devcode)
9. [Welcome email — admin-created accounts](#9-welcome-email--admin-created-accounts)
10. [Parent notification — attempt submission](#10-parent-notification--attempt-submission)
11. [Rate limiting & retry](#11-rate-limiting--retry)
12. [Error handling](#12-error-handling)
13. [Security & anti-phishing](#13-security--anti-phishing)
14. [Future v2: teacher & grading notifications](#14-future-v2-teacher--grading-notifications)
15. [Appendix: files, env vars, migrations](#15-appendix-files-env-vars-migrations)

---

## 1. Overview

Ba loại email ship trong v1, tất cả gửi qua Gmail SMTP dùng App Password.
Transport layer được bọc trong adapter để sau này đổi sang SendGrid / SES chỉ
là sửa một file.

```
Trigger event                BE                    Gmail SMTP             Recipient
     │                       │                          │                     │
 password/                   │                          │                     │
 request-code                │                          │                     │
     ├──────────────────────►│                          │                     │
     │                       │ 1. INSERT reset_code     │                     │
     │                       │ 2. silent 200 ──────────────────────────────────► FE shows toast
     │                       │ 3. BackgroundTask schedule                     │
     │                       │      EmailService.send() │                     │
     │                       ├─────────────────────────►│ (aiosmtplib STARTTLS) │
     │                       │                          ├────────────────────►│
     │                       │                          │   ~1-3s                │
     │                       │◄─────────────────────────┤ 250 OK              │
     │                       │ 4. log success           │                     │
```

Key properties:
- **Background send** — route trả 200 ngay; mail đi sau ~1–3s. User không chờ SMTP handshake.
- **Anti-enumeration preserved** — silent 200 dù email tồn tại hay không. BackgroundTask chỉ fire khi user thật.
- **Gmail App Password ở server-side** — không bao giờ lộ ra FE.
- **Adapter pattern** — `EmailService` ABC + `GmailSmtpAdapter`; provider mới cắm vào không đụng trigger code.
- **Dev mode** — `EMAIL_PROVIDER=noop` log message thay vì gửi; giữ `devCode` chảy về FE chỉ khi không có provider thật.

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Cách gửi mail | **Gmail SMTP + App Password** | Gmail API + OAuth2; SendGrid/Mailgun/Postmark; AWS SES | Zero new deps (aiosmtplib nhẹ), setup 5 phút, free 500/day (gmail) – 2000/day (Workspace). User chọn Google. OAuth2 overkill cho outbound-only. SendGrid/SES để dành v2. |
| 2 | Thời điểm gửi | **FastAPI BackgroundTasks** (gửi sau response) | Gửi đồng bộ trong route; job queue (Redis/ARQ, outbox table) | STARTTLS handshake ~1–3s sẽ làm route chậm. Queue thì over-engineer cho v1. Trade-off: mất task nếu process restart giữa chừng → chấp nhận (user bấm "Gửi lại mã"). |
| 3 | Template engine | **Python functions trả `(subject, text, html)`** | Jinja2 / template engine | Tránh thêm dependency; nội dung email ít & ổn định; f-string + shared HTML shell là đủ. |
| 4 | Định dạng email | **Two-part MIME (text + HTML)** | HTML-only; text-only | Text là fallback cho screen reader, CLI MUA, và giảm điểm spam (HTML-only trông spammy). |
| 5 | Xử lý lỗi gửi | **Retry trong adapter (3 lần, backoff 1/2/4s) + swallow ở background** | Bubble lỗi ra HTTP; không retry | Anti-enumeration buộc silent 200; email fail không được làm route trả non-2xx. Transient retry; permanent (auth/recipient) không retry. |
| 6 | Mất mail khi crash | **Chấp nhận loss-on-restart trong v1** | Persisted `email_outbox` + worker | Đơn giản hóa v1. Tài liệu hóa làm future hardening khi deploy nhiều. |
| 7 | `devCode` trong response | **Chỉ trả khi `EMAIL_PROVIDER=noop`** | Bỏ hẳn field; luôn trả | Giữ dev/test tiện, nhưng prod không bao giờ lộ OTP kể cả khi flag bị flip nhầm. |
| 8 | Rate limit | **Per-recipient 10/h + per-OTP-request 5/h** | Không giới hạn | Bảo vệ quota Gmail + chống spam inbox người nhận. |

---

## 3. Email types — v1 scope & v2 roadmap

### 3.1 v1 (implement now)

| # | Name | Trigger | Recipient | Template |
|---|------|---------|-----------|----------|
| E1 | **Password reset OTP** | `POST /api/auth/password/request-code` | User yêu cầu reset | `password_reset.{txt,html}` |
| E2 | **Welcome (admin-created account)** | `POST /api/admin/users` | User mới | `welcome.{txt,html}` |
| E3 | **Parent — child submitted attempt** | `POST /api/attempts/{id}/submit` (student có parent liên kết) | Parent qua `profiles.parent_id` | `parent_attempt_submitted.{txt,html}` |

### 3.2 v2 (roadmap — KHÔNG implement bây giờ)

Ghi lại để không mất design. Cả hai phụ thuộc feature chưa tồn tại trong v1.

| # | Name | Trigger (future) | Recipient | Notes |
|---|------|--------|-----------|-------|
| E4 | **Teacher — student submitted attempt** | `POST /api/attempts/{id}/submit` | Teacher được gán cho student | Cần `profiles.teacher_id` (NEW). Có thể digest mode tránh spam. |
| E5 | **Student — graded by teacher** | Khi teacher chấm tay attempt | Student | Cần manual-grading flow (writing/speaking). Không fire cho auto-graded. |

Cả hai reuse `EmailService` + adapter khi feature nền tảng có — không cần transport code mới.

---

## 4. Backend architecture (adapter pattern)

Cùng shape với `services/storage_service.py` và `services/translate_service.py`.

### 4.1 File structure

```
services/
├── email_service.py                # EmailService ABC + EmailMessage dataclass + factory + EmailType enum + render helpers
├── adapters/
│   ├── gmail_smtp_email.py         # GmailSmtpAdapter (aiosmtplib)
│   ├── sendgrid_email.py           # Stub — raise NotImplementedError
│   ├── ses_email.py                # Stub — raise NotImplementedError
│   └── noop_email.py               # NoopAdapter — logs the message, never sends. Tests + local dev.
└── email_templates/                # Plain Python source — no template engine
    ├── __init__.py
    ├── password_reset.py           # render(code, ttl_minutes) → (subject, text, html)
    ├── welcome.py                  # render(full_name, email, temp_password) → (...)
    └── parent_attempt_submitted.py # render(parent_name, child_name, exam_title, score, percentage) → (...)
```

### 4.2 Interface

```python
# services/email_service.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

class EmailType(str, Enum):
    PASSWORD_RESET = "password_reset"
    WELCOME = "welcome"
    PARENT_ATTEMPT_SUBMITTED = "parent_attempt_submitted"

@dataclass
class EmailMessage:
    to: list[str]
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    email_type: EmailType | None = None     # tagged for logging/metrics; not sent over wire

class EmailDeliveryError(Exception):
    """Raised by adapter after all retries fail. Background task catches and
    logs — never bubbles to the HTTP response."""

class EmailService(ABC):
    @abstractmethod
    async def send(self, message: EmailMessage) -> None:
        """Send `message`. Implementations retry on transient errors and raise
        EmailDeliveryError on permanent failure."""
        ...

def get_email_service() -> EmailService:
    """Factory — driven by EMAIL_PROVIDER env (default 'gmail_smtp').
    Use 'noop' in local dev / tests to skip real sending."""
    from config.settings import get_settings
    settings = get_settings()
    provider = getattr(settings, "email_provider", "gmail_smtp")
    if provider == "noop":
        from services.adapters.noop_email import NoopEmailAdapter
        return NoopEmailAdapter(settings)
    if provider == "sendgrid":
        from services.adapters.sendgrid_email import SendgridEmailAdapter
        return SendgridEmailAdapter(settings)
    if provider == "ses":
        from services.adapters.ses_email import SesEmailAdapter
        return SesEmailAdapter(settings)
    from services.adapters.gmail_smtp_email import GmailSmtpAdapter
    return GmailSmtpAdapter(settings)
```

### 4.3 Gmail SMTP adapter

```python
# services/adapters/gmail_smtp_email.py

import asyncio
import logging
from email.message import EmailMessage as MimeMessage
import aiosmtplib
from services.email_service import EmailMessage, EmailService, EmailDeliveryError

logger = logging.getLogger(__name__)

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587                       # STARTTLS
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [1, 2, 4]

class GmailSmtpAdapter(EmailService):
    def __init__(self, settings):
        user = getattr(settings, "gmail_smtp_user", None)
        password = getattr(settings, "gmail_smtp_password", None)
        if not user or not password:
            raise RuntimeError("GMAIL_SMTP_USER / GMAIL_SMTP_PASSWORD not set")
        self.user = user
        self.password = password
        self.from_name = getattr(settings, "email_from_name", "Mai Chi English")
        self.from_address = getattr(settings, "email_from_address", user)

    async def send(self, message: EmailMessage) -> None:
        mime = self._build_mime(message)
        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                await aiosmtplib.send(
                    mime,
                    hostname=GMAIL_SMTP_HOST,
                    port=GMAIL_SMTP_PORT,
                    start_tls=True,
                    username=self.user,
                    password=self.password,
                    timeout=10,
                )
                logger.info(
                    "email sent",
                    extra={"to": message.to, "type": message.email_type, "attempt": attempt + 1},
                )
                return
            except aiosmtplib.SMTPAuthenticationError:
                logger.exception("smtp auth failed — check GMAIL_SMTP_PASSWORD")
                raise EmailDeliveryError("authentication failed")
            except (aiosmtplib.SMTPException, asyncio.TimeoutError, OSError) as e:
                last_exc = e
                logger.warning("smtp transient error attempt=%d: %s", attempt + 1, e)
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(BACKOFF_SECONDS[attempt])
        raise EmailDeliveryError(f"failed after {MAX_ATTEMPTS} attempts: {last_exc}")

    def _build_mime(self, msg: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = f"{self.from_name} <{self.from_address}>"
        mime["To"] = ", ".join(msg.to)
        mime["Subject"] = msg.subject
        if msg.reply_to:
            mime["Reply-To"] = msg.reply_to
        for k, v in msg.headers.items():
            mime[k] = v
        mime.set_content(msg.text_body)
        if msg.html_body:
            mime.add_alternative(msg.html_body, subtype="html")
        return mime
```

### 4.4 Noop adapter (dev + tests)

```python
# services/adapters/noop_email.py
import logging
from services.email_service import EmailMessage, EmailService

logger = logging.getLogger(__name__)

class NoopEmailAdapter(EmailService):
    def __init__(self, settings):
        pass

    async def send(self, message: EmailMessage) -> None:
        logger.info("NOOP email send: to=%s subject=%r", message.to, message.subject)
        logger.debug("NOOP email text body:\n%s", message.text_body)
```

---

## 5. Background sending model

### 5.1 Why background

Gmail STARTTLS handshake + delivery ~1–3s. Await trong route sẽ block response —
`POST /api/auth/password/request-code` cảm giác chậm. FastAPI `BackgroundTasks`
chạy sau khi response đã gửi.

### 5.2 Dispatch helper

```python
# services/email_dispatch.py

from fastapi import BackgroundTasks
from services.email_service import EmailMessage, EmailDeliveryError, get_email_service
import logging

logger = logging.getLogger(__name__)

async def _send_and_swallow(message: EmailMessage) -> None:
    """Background entry point — never raises out of the task."""
    try:
        svc = get_email_service()
        await svc.send(message)
    except EmailDeliveryError as e:
        logger.error(
            "email delivery permanently failed",
            extra={"to": message.to, "type": message.email_type, "error": str(e)},
        )
    except Exception:
        logger.exception("unexpected error in background email send")

def schedule_email(background_tasks: BackgroundTasks, message: EmailMessage) -> None:
    """Schedule send-after-response. Call from any route."""
    background_tasks.add_task(_send_and_swallow, message)
```

### 5.3 Background vs queue — trade-off

`BackgroundTasks` chạy cùng process. Nếu process restart giữa lúc gửi response và
gửi email, task mất. v1 chấp nhận: mất OTP → user bấm "Gửi lại mã" sau 60s. Nếu
loss thành vấn đề thật (deploy nhiều), giới thiệu persisted queue (Redis + ARQ,
hoặc bảng `email_outbox` poll bởi worker). Future hardening — ngoài v1 scope.

---

## 6. Template strategy

### 6.1 Two-part MIME — text + HTML

Mọi loại email ship **cả** plain-text (`text/plain`) và HTML (`text/html`).
Client tự chọn cái render được. Plain text cũng là fallback cho screen reader,
CLI MUA, và spam-filter heuristics (HTML-only trông spammy).

### 6.2 Templates as Python functions

Không template engine. Mỗi loại email là một Python module trả
`(subject, text_body, html_body)`. f-string + shared HTML shell nhỏ. Copy
Vietnamese-first.

```python
# services/email_templates/password_reset.py

from textwrap import dedent
from services.email_templates._shared import wrap_html_shell

SUBJECT = "Mã đặt lại mật khẩu Mai Chi English"

def render(code: str, ttl_minutes: int) -> tuple[str, str, str]:
    text = dedent(f"""
        Xin chào,

        Mã đặt lại mật khẩu của bạn là: {code}

        Mã có hiệu lực trong {ttl_minutes} phút.
        Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.

        — Đội ngũ Mai Chi English
    """).strip()

    html_inner = f"""
        <p>Xin chào,</p>
        <p>Mã đặt lại mật khẩu của bạn là:</p>
        <p style="font-size:28px;font-weight:bold;letter-spacing:4px;
                  font-family:monospace;background:#f4f4f4;padding:12px 24px;
                  display:inline-block;border-radius:6px;">{code}</p>
        <p>Mã có hiệu lực trong <strong>{ttl_minutes} phút</strong>.</p>
        <p style="color:#666;font-size:14px;">
          Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.
        </p>
    """
    html = wrap_html_shell(html_inner)
    return SUBJECT, text, html
```

### 6.3 Shared HTML shell

```python
# services/email_templates/_shared.py

BASE_STYLE = """
  body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         color: #222; line-height: 1.6; padding: 24px; max-width: 560px; }
  .footer { color: #888; font-size: 12px; margin-top: 32px; border-top: 1px solid #eee; padding-top: 16px; }
"""

def wrap_html_shell(inner_html: str) -> str:
    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <style>{BASE_STYLE}</style>
</head>
<body>
  <h2 style="color:#0066cc;margin-top:0;">Mai Chi English</h2>
  {inner_html}
  <div class="footer">
    Email tự động — vui lòng không trả lời.<br>
    © Mai Chi English Center
  </div>
</body>
</html>"""
```

Inline styles only — nhiều email client strip `<style>` blocks. Shell dùng
`<style>` (chạy trên Gmail/Outlook 365) + inline styles ở chỗ quan trọng (OTP box).
Test với Gmail web, Gmail mobile, Outlook 365, Apple Mail — Litmus / Mailtrap nếu
cần post-launch.

### 6.4 Template list

| Module | Function | Inputs | Subject |
|---|---|---|---|
| `password_reset.py` | `render(code, ttl_minutes)` | 6-digit code, TTL phút | `Mã đặt lại mật khẩu Mai Chi English` |
| `welcome.py` | `render(full_name, email, temp_password)` | new user data | `Chào mừng đến với Mai Chi English` |
| `parent_attempt_submitted.py` | `render(parent_name, child_name, exam_title, score, percentage)` | submission summary | `Con bạn vừa hoàn thành bài thi — {exam_title}` |

---

## 7. Gmail App Password setup

One-time setup; làm một lần cho mỗi Gmail account dùng để gửi.

| Step | Action |
|---|---|
| 1 | Đăng nhập Gmail account sẽ gửi mail (vd `mai.chi.english@gmail.com`). |
| 2 | Vào **Google Account → Security**. |
| 3 | Bật **2-Step Verification** (bắt buộc — App Password chỉ chạy với 2FA). |
| 4 | Mở **App passwords** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)). |
| 5 | Tạo password mới: app name = `Mai Chi English Backend`. |
| 6 | Copy chuỗi 16 ký tự (chỉ hiện 1 lần — lưu ngay vào Render env vars). |
| 7 | Set trên Render → Environment: `GMAIL_SMTP_USER=<gmail>`, `GMAIL_SMTP_PASSWORD=<16-char>`. |
| 8 | (Optional) `EMAIL_FROM_NAME="Mai Chi English"`, `EMAIL_FROM_ADDRESS=<same as user>`. |

**Nếu App Password bị lộ**: revoke trên cùng trang; tạo mới; update Render env.
Code cũ ngừng chạy ngay.

---

## 8. Password reset flow — thay devCode

### 8.1 Current state

`services/auth_service.request_password_reset_code` trả struct chứa `devCode`
(OTP cleartext) để dev/staging test mà không cần email infra. Production hiện
không có cách giao code.

### 8.2 Change

```python
# api/auth/routes.py  (sketch — chỉ phần thay đổi)

from fastapi import BackgroundTasks
from services.email_service import EmailMessage, EmailType
from services.email_dispatch import schedule_email
from services.email_templates import password_reset

@router.post("/password/request-code")
async def request_reset_code(
    request: PasswordResetCodeRequest,
    background_tasks: BackgroundTasks,
):
    result = await auth_service.request_password_reset_code(request.email)

    # Anti-enumeration: silent 200 bất kể. devCode chỉ phát khi noop.
    if result.user_exists:
        subject, text, html = password_reset.render(code=result.code, ttl_minutes=10)
        schedule_email(background_tasks, EmailMessage(
            to=[request.email],
            subject=subject,
            text_body=text,
            html_body=html,
            email_type=EmailType.PASSWORD_RESET,
        ))

    response = PasswordResetCodeResponse(
        status=200,
        data=PasswordResetCodeResponseData(
            message="If the email exists, a reset code has been sent.",
        ),
    )

    from config.settings import get_settings
    if get_settings().email_provider == "noop":
        response.data.devCode = result.code

    return response
```

### 8.3 Backward-compat note

Field `devCode` vẫn trong response schema nhưng thành **conditional**:
- `EMAIL_PROVIDER=noop` (dev/test) → `devCode` có giá trị.
- Provider khác → field absent hoặc `null`.

FE nên đã code để handle absence. Nếu FE phụ thuộc `devCode` cách khác, đó là FE
bug cần fix khi rollout. (Chi tiết FE: xem [Frontend notes](email-frontend.md).)

### 8.4 Rate limit on requests (anti-spam)

User không được spam OTP request (đốt quota Gmail + inbox người nhận).
Xem [§11.2](#112-rate-limit-per-otp-request).

---

## 9. Welcome email — admin-created accounts

### 9.1 Trigger

`POST /api/admin/users` hiện tạo profile + password generated (trả trong response
để admin đưa user). Với v1, email cũng tới inbox user mới.

### 9.2 Change

```python
# api/admin/routes.py  (sketch)

@router.post("/users", ...)
async def admin_create_user(request: AdminCreateUserRequest, background_tasks: BackgroundTasks):
    user, temp_password = await user_service.create_user(...)
    subject, text, html = welcome.render(
        full_name=user.full_name, email=user.email, temp_password=temp_password,
    )
    schedule_email(background_tasks, EmailMessage(
        to=[user.email], subject=subject, text_body=text, html_body=html,
        email_type=EmailType.WELCOME,
    ))
    return ...
```

### 9.3 Email chứa gì

- Tên user · Email đăng nhập · Mật khẩu tạm (cleartext — kênh an toàn duy nhất lúc account vừa tạo) · Link trang đăng nhập · Hướng dẫn đổi mật khẩu sau lần đầu.

### 9.4 Email KHÔNG chứa

- Link "auto-login" có token (phishing + token leak) · Tên admin tạo · Info user khác.

### 9.5 Admin response vẫn trả temp_password

Dù email đã đi, response admin giữ `tempPassword` để admin giao tay nếu email fail.
Belt + braces.

---

## 10. Parent notification — attempt submission

### 10.1 Trigger

`POST /api/attempts/{id}/submit` — khi `profiles.parent_id` được set, fire
notification tới email parent sau khi attempt được chấm.

### 10.2 Where to schedule

Trong `attempt_service.submit_attempt`, sau khi attempt finalized nhưng trong cùng
request lifecycle để `BackgroundTasks` còn available. Route truyền xuống.

```python
# api/attempts/routes.py
@router.post("/{attempt_id}/submit", ...)
async def submit(attempt_id: UUID, request: AttemptSubmitRequest,
                 background_tasks: BackgroundTasks, current_user = Depends(get_current_user)):
    result = await attempt_service.submit_attempt(
        attempt_id, current_user.id, request, background_tasks=background_tasks,
    )
    return ...
```

```python
# services/attempt_service.py  (sketch — cuối submit_attempt)
parent = await user_service.get_parent_of(student_id=current_user.id)
if parent and parent.email:
    subject, text, html = parent_attempt_submitted.render(
        parent_name=parent.full_name, child_name=current_user.full_name,
        exam_title=exam.title, score=result.score, percentage=result.percentage,
    )
    schedule_email(background_tasks, EmailMessage(
        to=[parent.email], subject=subject, text_body=text, html_body=html,
        email_type=EmailType.PARENT_ATTEMPT_SUBMITTED,
    ))
```

### 10.3 Email chứa gì

- Tên phụ huynh (lời chào) · Tên con · Đề thi (title + level) · Điểm + phần trăm · Link `GET /api/attempts/{id}` (parent role xem được) · Không kèm bản dịch chi tiết câu sai.

### 10.4 Email KHÔNG chứa

- Per-question grading · Điểm con khác · Admin/teacher notes.

### 10.5 Edge case — abandoned attempt

`POST /api/attempts/{id}/abandon` KHÔNG fire parent email. Score 0 từ abandon là
noise không nên báo phụ huynh. Chỉ `submit` thành công mới trigger.

---

## 11. Rate limiting & retry

### 11.1 Rate limit per recipient (global)

Cap mỗi địa chỉ email tối đa **10 email/giờ** mọi loại. Chống trigger lỗi spam ai
đó + giữ dưới ngưỡng throttle per-recipient của Gmail.

```sql
-- migrations/0011_email_send_log.sql
CREATE TABLE IF NOT EXISTS public.email_send_log (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient       text NOT NULL,
  email_type      text NOT NULL,
  status          text NOT NULL,        -- 'sent' | 'failed'
  sent_at         timestamptz NOT NULL DEFAULT now(),
  error_message   text                  -- nullable
);
CREATE INDEX IF NOT EXISTS email_send_log_recipient_sent_at_idx
  ON public.email_send_log (recipient, sent_at DESC);
```

```python
async def can_send_to(recipient: str, *, limit: int = 10, window_hours: int = 1) -> bool:
    count = await db.fetchval(
        "SELECT COUNT(*) FROM public.email_send_log "
        "WHERE recipient = $1 AND status = 'sent' "
        "AND sent_at > now() - $2::interval",
        recipient, f"{window_hours} hours",
    )
    return count < limit
```

`can_send_to` False → log + skip. Không raise — silent drop là đúng cho notification.

### 11.2 Rate limit per OTP request

User request OTP tối đa **5 lần/giờ/email**. Quá → BE silent no-op (vẫn 200 — anti-enumeration).

Implementation: đếm rows trong `password_reset_codes` (đã tồn tại) với
`created_at > now() - interval '1 hour'` cho email đó. Quá threshold → skip cả
insert lẫn email schedule.

### 11.3 Retry trong adapter

`GmailSmtpAdapter` retry tối đa 3 lần backoff 1s/2s/4s cho **transient**:
`SMTPException` (không phải auth), `asyncio.TimeoutError`, `OSError`.

**Permanent** KHÔNG retry: `SMTPAuthenticationError` (sai app password — fix env),
`SMTPRecipientsRefused` (recipient sai), `SMTPSenderRefused` (account disabled/quota).

### 11.4 No queue-and-redeliver in v1

Nếu cả 3 retry fail, email bị drop. Documented limitation. v2 hardening: persist
`email_outbox` + worker retry với backoff dài hơn.

---

## 12. Error handling

### 12.1 Background tasks không bubble ra HTTP

Dispatcher catch mọi exception, log, return. User đã thấy 200. Audit trail duy nhất
là log + `email_send_log` row.

### 12.2 Adapter failure modes

| Error | Mapped behavior |
|---|---|
| `SMTPAuthenticationError` | Adapter raise `EmailDeliveryError` ngay. Log `ERROR` — paging-level. Fix: rotate App Password. |
| `SMTPRecipientsRefused` | Adapter raise `EmailDeliveryError`. Log `WARN` — typo recipient. |
| `SMTPException` (other) / `TimeoutError` / `OSError` | Retry tối đa 3 lần. Vẫn fail → `EmailDeliveryError` + log `ERROR`. |
| Adapter init `RuntimeError` (env missing) | Background task log và bỏ — dispatcher catch hết. Surface ở startup-time validation ([§12.3](#123-startup-time-config-check)). |

### 12.3 Startup-time config check

Trong `main.py` lifespan: khi `EMAIL_PROVIDER != "noop"`, thử `get_email_service()`
một lần lúc boot. `RuntimeError` → log `WARN` banner ("email disabled — sending
stubbed") và force fall back về `NoopEmailAdapter`. App vẫn boot; admin biết sớm
thay vì phát hiện khi OTP đầu tiên không tới.

### 12.4 No 5xx leak to FE on email failure

Anti-enumeration đã buộc silent 200. Email failure KHÔNG BAO GIỜ gây non-2xx từ
bất kỳ route. Background design enforce điều này về mặt cấu trúc.

---

## 13. Security & anti-phishing

### 13.1 OTP email content rules

| Rule | Reason |
|---|---|
| Subject KHÔNG chứa code | Notification preview trên iOS/Android hiện subject — code sẽ lộ. |
| Body không có clickable link | OTP flow không cần; link là phishing surface. |
| Body có disclaimer "nếu bạn không yêu cầu" | Recovery-flow hygiene chuẩn. |
| TTL ghi rõ (10 phút) | Đặt kỳ vọng người nhận; giảm bối rối khi hết hạn. |
| Không lộ password cũ / hash | Codified trong template review checklist. |

### 13.2 Welcome email content rules

| Rule | Reason |
|---|---|
| Temp password gửi qua email — chấp nhận risk | Kênh duy nhất giao account mới. Force-change lần đầu giảm cửa sổ phơi nhiễm. |
| Không clickable login link kèm token | Cùng phishing surface. Plain URL trang login OK. |
| Không nhắc ai tạo account | Admin email là nội bộ — không liên quan + lộ cơ cấu tổ chức. |

### 13.3 Parent notification content rules

| Rule | Reason |
|---|---|
| Chỉ tên con + tên đề + điểm | Tối thiểu để hữu ích. Không per-question. |
| Link detail page yêu cầu parent login | Email không phải credential. |
| Chỉ gửi email parent of record | Không bao giờ CC parent/teacher khác. |

### 13.4 Email-spoof / sender-validation

v1 (personal Gmail): Google tự xử SPF/DKIM cho `gmail.com`. Khi migrate custom
domain (Workspace), set:
- **SPF**: `v=spf1 include:_spf.google.com ~all`
- **DKIM**: enable trong Workspace admin
- **DMARC**: `v=DMARC1; p=quarantine; rua=mailto:postmaster@<domain>`

Ngoài v1 scope, ghi cho domain switch sau.

### 13.5 PII in logs

`email_send_log` lưu recipient email (PII). Chấp nhận cho ops debug trong admin
team. KHÔNG log email body/subject. Structured logger emit `to=[email]` và
`type=password_reset` thôi.

---

## 14. Future v2: teacher & grading notifications

> **Status**: design only. Không implement v1. Cả hai phụ thuộc feature chưa tồn
> tại (teacher↔student assignment, manual-grading workflow).

### 14.1 E4 — Teacher: student submitted attempt

Trigger giống parent notification (`POST /attempts/{id}/submit`), recipient là
teacher được gán cho student.

**Blockers**: chưa có `profiles.teacher_id` (migration thêm). Teacher 30 students
có thể bị flood nếu cả lớp nộp cùng lúc → **digest mode**: buffer per teacher,
flush mỗi N giờ thành 1 summary email. Đánh đổi real-time lấy sanity.

Template idea: `teacher_attempt_summary.{txt,html}`.

### 14.2 E5 — Student: graded by teacher

Trigger: khi teacher chấm tay attempt (route chưa tồn tại, vd `POST /api/attempts/{id}/grade`).

**Blockers**: manual-grading flow tự nó là v2 (KET/PET writing/speaking cần). Cần
quyết: notify mỗi grade event hay chỉ khi attempt finalized? Có lẽ cái sau.

Template idea: `student_graded.{txt,html}`.

### 14.3 Implementation cost khi v2 lands

Reuse v1 transport (`EmailService` + adapter + `schedule_email`). New work: 2
template modules, 2 trigger sites, có thể digest buffer cho E4. ~1–2 ngày mỗi cái.

---

## 15. Appendix: files, env vars, migrations

### 15.1 New files

| File | Purpose |
|------|---------|
| `services/email_service.py` | `EmailService` ABC + `EmailMessage` + `EmailType` + `EmailDeliveryError` + `get_email_service()` |
| `services/email_dispatch.py` | `schedule_email()` + `_send_and_swallow()` |
| `services/email_send_log_service.py` | `record()`, `can_send_to()` rate-limit queries |
| `services/adapters/gmail_smtp_email.py` | `GmailSmtpAdapter` (aiosmtplib + retry) |
| `services/adapters/noop_email.py` | `NoopEmailAdapter` (logs only) |
| `services/adapters/sendgrid_email.py` | Stub — `NotImplementedError` |
| `services/adapters/ses_email.py` | Stub |
| `services/email_templates/__init__.py` | Re-exports renderers |
| `services/email_templates/_shared.py` | `wrap_html_shell()`, `BASE_STYLE` |
| `services/email_templates/password_reset.py` | `render(code, ttl_minutes)` |
| `services/email_templates/welcome.py` | `render(full_name, email, temp_password)` |
| `services/email_templates/parent_attempt_submitted.py` | `render(parent_name, child_name, exam_title, score, percentage)` |
| `config/settings.py` | Add `email_provider`, `gmail_smtp_user`, `gmail_smtp_password`, `email_from_name`, `email_from_address` |
| `.env.example` | Add email env vars |
| `requirements.txt` | Add `aiosmtplib>=3.0,<4` |

### 15.2 Modified files

| File | Change |
|------|---------|
| `api/auth/routes.py` | `request_password_reset_code` schedules email; `devCode` chỉ khi `EMAIL_PROVIDER=noop` |
| `api/admin/routes.py` | `admin_create_user` schedules welcome email |
| `services/attempt_service.py` | `submit_attempt` schedules parent notification khi parent linked |
| `main.py` | Lifespan: validate email provider lúc boot; fallback noop + WARN nếu misconfigured |
| `api/auth/schemas.py` | `devCode` thành `Optional[str] = None` |

### 15.3 New migration

`migrations/0011_email_send_log.sql` — `CREATE TABLE email_send_log` per
[§11.1](#111-rate-limit-per-recipient). Idempotent. `schema.sql` update lockstep
để `scripts/init_schema.py --drop` ra state khớp.

### 15.4 New env vars

| Name | Required | Default | Notes |
|---|---|---|---|
| `EMAIL_PROVIDER` | no | `gmail_smtp` | `gmail_smtp` \| `noop` \| `sendgrid` (stub) \| `ses` (stub) |
| `GMAIL_SMTP_USER` | yes (if gmail_smtp) | — | Gmail address gửi. |
| `GMAIL_SMTP_PASSWORD` | yes (if gmail_smtp) | — | 16-char App Password (KHÔNG phải account password). |
| `EMAIL_FROM_NAME` | no | `Mai Chi English` | Display name trong From. |
| `EMAIL_FROM_ADDRESS` | no | fallback `GMAIL_SMTP_USER` | Khi gửi from-address khác SMTP login. |

### 15.5 Local dev setup

```env
# .env (local)
EMAIL_PROVIDER=noop
```

Không SMTP thật. `devCode` chảy về FE. `email_send_log` rows ghi `status=sent` để
rate-limit logic vẫn được exercise.

### 15.6 Production setup checklist

1. Chọn/tạo Gmail account gửi (`mai.chi.english@gmail.com`).
2. Bật 2FA.
3. Generate App Password ([§7](#7-gmail-app-password-setup)).
4. Set Render env: `EMAIL_PROVIDER=gmail_smtp`, `GMAIL_SMTP_USER`, `GMAIL_SMTP_PASSWORD`, `EMAIL_FROM_NAME`, `EMAIL_FROM_ADDRESS`.
5. Deploy.
6. Smoke test: trigger forgot-password cho test account; verify inbox.
7. Confirm `devCode` absent trong prod response.
8. Confirm log có `email sent` info line.
