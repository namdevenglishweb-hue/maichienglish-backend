import logging
import unicodedata
from typing import Any, Optional

from services.exceptions import (
    AlreadyExistsError,
    InvalidCredentialsError,
    NotFoundError,
    ValidationError,
)
from utils.password_utils import hash_password, verify_password

logger = logging.getLogger(__name__)


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    email = unicodedata.normalize("NFKC", email)
    if "+" in email:
        local, domain = email.rsplit("@", 1)
        local = local.split("+")[0]
        email = f"{local}@{domain}"
    return email


def _row_to_user(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "password_hash": row["password_hash"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "role": row["role"],
        "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
        "tier": row["tier"] or "free",
        "subscription_status": row["sub_status"],
        "credits_monthly": row["credits_monthly"] or 0,
        "credits_remaining": row["credits_remaining"] or 0,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


class UserService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: str = "student",
        phone: Optional[str] = None,
        tier: str = "free",
        parent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        email = _normalize_email(email)
        password_hash_value = hash_password(password)

        # parent_id only meaningful for students; silently ignore otherwise.
        if parent_id and role != "student":
            parent_id = None

        async with self.db.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT id FROM public.profiles WHERE email = $1",
                    email,
                )
                if existing:
                    logger.warning("Attempted to create duplicate user: %s", email)
                    raise AlreadyExistsError(
                        f"User with email {email} already exists"
                    )

                if parent_id:
                    await self._assert_role(conn, parent_id, "parent")

                row = await conn.fetchrow(
                    """
                    INSERT INTO public.profiles
                        (email, password_hash, full_name, phone, role, parent_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, email, full_name, phone, role, parent_id, created_at
                    """,
                    email,
                    password_hash_value,
                    full_name,
                    phone,
                    role,
                    parent_id,
                )

                await conn.execute(
                    """
                    INSERT INTO public.subscriptions (user_id, tier, status)
                    VALUES ($1, $2, 'active')
                    """,
                    row["id"],
                    tier,
                )

        logger.info("Created user: %s (role=%s, tier=%s)", email, role, tier)
        return {
            "id": str(row["id"]),
            "email": row["email"],
            "full_name": row["full_name"],
            "phone": row["phone"],
            "role": row["role"],
            "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
            "tier": tier,
            "created_at": row["created_at"].isoformat(),
        }

    async def link_parent(
        self, student_id: str, parent_id: Optional[str]
    ) -> dict[str, Any]:
        """Set or clear `parent_id` on a student row.

        Args:
            student_id: UUID of the student profile to update.
            parent_id: UUID of the parent profile, or `None` to unlink.

        Raises:
            NotFoundError: if `student_id` doesn't exist.
            ValidationError: if target isn't a student, or `parent_id` isn't a parent.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._assert_role(conn, student_id, "student")
                if parent_id:
                    await self._assert_role(conn, parent_id, "parent")

                row = await conn.fetchrow(
                    """
                    UPDATE public.profiles
                    SET parent_id = $2
                    WHERE id = $1
                    RETURNING id, role, parent_id
                    """,
                    student_id,
                    parent_id,
                )

        logger.info(
            "Linked student %s to parent %s", student_id, parent_id or "<none>"
        )
        return {
            "id": str(row["id"]),
            "role": row["role"],
            "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
        }

    async def _assert_role(self, conn, user_id: str, expected_role: str) -> None:
        actual = await conn.fetchval(
            "SELECT role FROM public.profiles WHERE id = $1", user_id
        )
        if actual is None:
            logger.warning("_assert_role: user %s not found", user_id)
            raise NotFoundError(f"User {user_id} not found")
        if actual != expected_role:
            logger.warning(
                "_assert_role: user %s has role '%s', expected '%s'",
                user_id,
                actual,
                expected_role,
            )
            raise ValidationError(
                f"User {user_id} has role '{actual}', expected '{expected_role}'"
            )

    async def authenticate(self, email: str, password: str) -> dict[str, Any]:
        email = _normalize_email(email)
        user = await self._get_by_email_with_subscription(email)
        if not user:
            logger.warning("Login attempt for non-existent user: %s", email)
            raise InvalidCredentialsError("Invalid email or password")

        if not verify_password(password, user["password_hash"]):
            logger.warning("Failed login for: %s", email)
            raise InvalidCredentialsError("Invalid email or password")

        logger.info("Successful login: %s", email)
        return user

    async def get_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return await self._get_by_email_with_subscription(_normalize_email(email))

    async def get_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.id, p.email, p.password_hash, p.full_name, p.phone,
                       p.role, p.parent_id, p.created_at,
                       s.tier, s.status AS sub_status,
                       s.credits_monthly, s.credits_remaining
                FROM public.profiles p
                LEFT JOIN public.subscriptions s ON s.user_id = p.id
                WHERE p.id = $1
                """,
                user_id,
            )
            return _row_to_user(row) if row else None

    async def delete_user(self, user_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.profiles WHERE id = $1",
                user_id,
            )
        # asyncpg returns "DELETE <n>" — parse the count
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            logger.warning("delete_user: user %s not found", user_id)
            raise NotFoundError(f"User {user_id} not found")
        logger.info("Deleted user %s", user_id)

    async def admin_reset_password(self, user_id: str, new_password: str) -> None:
        new_hash = hash_password(new_password)
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "UPDATE public.profiles SET password_hash = $2 WHERE id = $1",
                user_id,
                new_hash,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            logger.warning("admin_reset_password: user %s not found", user_id)
            raise NotFoundError(f"User {user_id} not found")
        logger.info("Admin reset password for user %s", user_id)

    async def _get_by_email_with_subscription(
        self, email: str
    ) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.id, p.email, p.password_hash, p.full_name, p.phone,
                       p.role, p.parent_id, p.created_at,
                       s.tier, s.status AS sub_status,
                       s.credits_monthly, s.credits_remaining
                FROM public.profiles p
                LEFT JOIN public.subscriptions s ON s.user_id = p.id
                WHERE p.email = $1
                """,
                email,
            )
            return _row_to_user(row) if row else None


user_service = UserService()
