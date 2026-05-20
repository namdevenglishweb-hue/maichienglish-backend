import logging
from typing import Any, Optional

from services.exceptions import NotFoundError, ValidationError
from services.subscription_plans import SUBSCRIPTION_PLANS, PlanTier

logger = logging.getLogger(__name__)

_VALID_TIERS = {t.value for t in PlanTier}


def _row_to_subscription(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "tier": row["tier"],
        "status": row["status"],
        "credits_monthly": row["credits_monthly"],
        "credits_remaining": row["credits_remaining"],
        "current_period_start": row["current_period_start"].isoformat()
        if row["current_period_start"]
        else None,
        "current_period_end": row["current_period_end"].isoformat()
        if row["current_period_end"]
        else None,
    }


class SubscriptionService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def get_by_user_id(self, user_id: str) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, tier, status,
                       credits_monthly, credits_remaining,
                       current_period_start, current_period_end
                FROM public.subscriptions
                WHERE user_id = $1
                """,
                user_id,
            )
            return _row_to_subscription(row) if row else None

    async def update_tier(self, user_id: str, new_tier: str) -> dict[str, Any]:
        if new_tier not in _VALID_TIERS:
            logger.warning(
                "update_tier: invalid tier '%s' requested for user %s",
                new_tier,
                user_id,
            )
            raise ValidationError(
                f"Invalid tier '{new_tier}'. Must be one of: {sorted(_VALID_TIERS)}"
            )

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.subscriptions
                SET tier = $2, status = 'active', updated_at = now()
                WHERE user_id = $1
                RETURNING id, user_id, tier, status,
                          credits_monthly, credits_remaining,
                          current_period_start, current_period_end
                """,
                user_id,
                new_tier,
            )
            if not row:
                logger.warning("update_tier: no subscription for user %s", user_id)
                raise NotFoundError(f"No subscription found for user {user_id}")

        logger.info("Updated subscription for user %s to tier %s", user_id, new_tier)
        return _row_to_subscription(row)


subscription_service = SubscriptionService()


def list_plans() -> list[dict[str, Any]]:
    """Return all subscription plans as serializable dicts."""
    out = []
    for plan in SUBSCRIPTION_PLANS.values():
        out.append(
            {
                "tier": plan.tier.value,
                "name": plan.name,
                "priceMonthly": plan.price_monthly,
                "attemptsMonthly": plan.attempts_monthly,
                "features": [
                    {
                        "key": f.key,
                        "name": f.name,
                        "active": f.active,
                        "value": f.value,
                    }
                    for f in plan.features
                ],
            }
        )
    return out
