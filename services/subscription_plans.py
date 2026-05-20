from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
    attempts_monthly: int  # -1 = unlimited
    features: list[PlanFeature] = field(default_factory=list)


SUBSCRIPTION_PLANS: dict[PlanTier, SubscriptionPlan] = {
    PlanTier.FREE: SubscriptionPlan(
        tier=PlanTier.FREE,
        name="Free",
        price_monthly=0.0,
        attempts_monthly=5,
        features=[
            PlanFeature("take_exams", "Take Exams", True, "5/month"),
            PlanFeature("view_results", "View Results", True),
            PlanFeature("full_history", "Full History", False),
            PlanFeature("progress_charts", "Progress Charts", False),
            PlanFeature("export_results", "Export Results", False),
        ],
    ),
    PlanTier.BASIC: SubscriptionPlan(
        tier=PlanTier.BASIC,
        name="Basic",
        price_monthly=9.99,
        attempts_monthly=50,
        features=[
            PlanFeature("take_exams", "Take Exams", True, "50/month"),
            PlanFeature("view_results", "View Results", True),
            PlanFeature("full_history", "Full History", True),
            PlanFeature("progress_charts", "Progress Charts", True),
            PlanFeature("export_results", "Export Results", False),
        ],
    ),
    PlanTier.PRO: SubscriptionPlan(
        tier=PlanTier.PRO,
        name="Pro",
        price_monthly=19.99,
        attempts_monthly=-1,
        features=[
            PlanFeature("take_exams", "Take Exams", True, "Unlimited"),
            PlanFeature("view_results", "View Results", True),
            PlanFeature("full_history", "Full History", True),
            PlanFeature("progress_charts", "Progress Charts", True),
            PlanFeature("export_results", "Export Results", True),
        ],
    ),
    PlanTier.ULTRA: SubscriptionPlan(
        tier=PlanTier.ULTRA,
        name="Ultra (Admin)",
        price_monthly=0.0,
        attempts_monthly=-1,
        features=[
            PlanFeature("take_exams", "Take Exams", True, "Unlimited"),
            PlanFeature("view_results", "View Results", True),
            PlanFeature("full_history", "Full History", True),
            PlanFeature("progress_charts", "Progress Charts", True),
            PlanFeature("export_results", "Export Results", True),
            PlanFeature("manage_exams", "Manage Exams", True),
            PlanFeature("manage_users", "Manage Users", True),
            PlanFeature("manage_subscriptions", "Manage Subscriptions", True),
            PlanFeature("view_all_students", "View All Students", True),
        ],
    ),
}
