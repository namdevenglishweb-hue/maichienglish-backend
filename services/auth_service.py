import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.exceptions import ValidationError
from services.user_service import _normalize_email, user_service
from utils.password_utils import hash_password, verify_password

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)


def _generate_code() -> str:
    """Return a 6-digit numeric reset code, zero-padded."""
    return f"{secrets.randbelow(1_000_000):06d}"


class AuthService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def request_password_reset_code(self, email: str) -> dict[str, Any]:
        """Issue a fresh reset code for `email`.

        Returns a dict with `expires_in_seconds` and `code` (None if the email
        doesn't exist — caller still returns 200 to prevent enumeration).
        """
        email = _normalize_email(email)
        user = await user_service.get_by_email(email)
        if not user:
            logger.info("request_password_reset_code: unknown email %s (silent 200)", email)
            return {"code": None, "expires_in_seconds": int(CODE_TTL.total_seconds())}

        code = _generate_code()
        code_hash = hash_password(code)
        expires_at = datetime.now(timezone.utc) + CODE_TTL

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Invalidate any previously issued, unused codes for this user.
                await conn.execute(
                    """
                    UPDATE public.password_reset_codes
                    SET used_at = now()
                    WHERE user_id = $1 AND used_at IS NULL
                    """,
                    user["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO public.password_reset_codes
                        (user_id, code_hash, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    user["id"],
                    code_hash,
                    expires_at,
                )

        logger.info("Issued password reset code for user %s", user["id"])
        return {"code": code, "expires_in_seconds": int(CODE_TTL.total_seconds())}

    async def reset_password(self, email: str, code: str, new_password: str) -> None:
        """Verify the code and replace the password hash.

        Raises:
            ValidationError: email not found, or code invalid / expired / already used.
        """
        email = _normalize_email(email)
        user = await user_service.get_by_email(email)
        if not user:
            logger.warning("reset_password: unknown email %s", email)
            raise ValidationError("Invalid or expired code")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Pull all candidate codes — typically 1 active row after invalidation.
                rows = await conn.fetch(
                    """
                    SELECT id, code_hash, expires_at
                    FROM public.password_reset_codes
                    WHERE user_id = $1
                      AND used_at IS NULL
                      AND expires_at > now()
                    ORDER BY created_at DESC
                    """,
                    user["id"],
                )
                if not rows:
                    logger.warning("reset_password: no active code for user %s", user["id"])
                    raise ValidationError("Invalid or expired code")

                matched_id = None
                for r in rows:
                    if verify_password(code, r["code_hash"]):
                        matched_id = r["id"]
                        break

                if matched_id is None:
                    logger.warning("reset_password: code mismatch for user %s", user["id"])
                    raise ValidationError("Invalid or expired code")

                # Atomically: mark code used + update password.
                new_hash = hash_password(new_password)
                await conn.execute(
                    "UPDATE public.password_reset_codes SET used_at = now() WHERE id = $1",
                    matched_id,
                )
                await conn.execute(
                    "UPDATE public.profiles SET password_hash = $2 WHERE id = $1",
                    user["id"],
                    new_hash,
                )

        logger.info("Password reset successful for user %s", user["id"])


auth_service = AuthService()
