"""
Quota management for user usage limits

This module provides quota checking and plan limit enforcement for OpenClaw users.
Supports free/pro/enterprise plans with monthly token limits.
"""

from datetime import datetime
from typing import Optional, Dict
import logging

from app.database import get_db_connection

logger = logging.getLogger(__name__)

# Plan limits (tokens per month)
PLAN_LIMITS = {
    "free": {
        "tokens_per_month": 100_000,      # 100K tokens/month
        "max_instances": 1,                # Max 1 instance
        "max_memory_per_instance": "4Gi",
        "max_cpu_per_instance": "2",
        "price_monthly": 0,
        "features": ["Community support", "Basic models", "1 instance"]
    },
    "pro": {
        "tokens_per_month": 10_000_000,   # 10M tokens/month
        "max_instances": 5,                # Max 5 instances
        "max_memory_per_instance": "8Gi",
        "max_cpu_per_instance": "4",
        "price_monthly": 99,
        "features": ["Priority support", "All models", "5 instances", "Advanced features"]
    },
    "enterprise": {
        "tokens_per_month": None,          # Unlimited
        "max_instances": None,             # Unlimited
        "max_memory_per_instance": "16Gi",
        "max_cpu_per_instance": "8",
        "price_monthly": None,             # Custom pricing
        "features": ["Dedicated support", "Custom deployment", "Unlimited instances", "SLA guarantee"]
    },
}


class QuotaStatus:
    """Quota status for a user"""

    def __init__(
        self,
        user_email: str,
        plan: str,
        current_usage: int,
        limit: Optional[int],
        percentage_used: float
    ):
        self.user_email = user_email
        self.plan = plan
        self.current_usage = current_usage
        self.limit = limit
        self.percentage_used = percentage_used

        # Determine status
        self.is_warning = limit is not None and percentage_used >= 80.0
        self.is_over_quota = limit is not None and current_usage >= limit

        # Status emoji
        if self.is_over_quota:
            self.status_emoji = "🔴"
            self.status_text = "Over quota"
        elif self.is_warning:
            self.status_emoji = "🟡"
            self.status_text = "Warning"
        else:
            self.status_emoji = "🟢"
            self.status_text = "Within limit"

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "user_email": self.user_email,
            "plan": self.plan,
            "current_usage": self.current_usage,
            "limit": self.limit,
            "percentage_used": round(self.percentage_used, 2),
            "is_warning": self.is_warning,
            "is_over_quota": self.is_over_quota,
            "status_emoji": self.status_emoji,
            "status_text": self.status_text,
        }


def get_monthly_usage(user_id: str) -> int:
    """
    Get total token usage for current month

    Args:
        user_id: User ID (hashed email)

    Returns:
        Total tokens used this month
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get first day of current month
    now = datetime.utcnow()
    month_start = f"{now.year}-{now.month:02d}-01"

    try:
        cursor.execute('''
            SELECT COALESCE(SUM(total_tokens), 0)
            FROM usage_events
            WHERE tenant_id = %s AND timestamp >= %s
        ''', (user_id, month_start))

        total = cursor.fetchone()[0]
        conn.close()

        return total or 0

    except Exception as e:
        logger.error(f"Failed to get monthly usage for {user_id}: {e}")
        conn.close()
        return 0


def check_quota(user_email: str, plan: str = "free") -> QuotaStatus:
    """
    Check if user is within quota

    Args:
        user_email: User email address
        plan: User's plan (free/pro/enterprise)

    Returns:
        QuotaStatus object with usage info
    """
    from app.utils.user_id import generate_user_id

    user_id = generate_user_id(user_email)
    current_usage = get_monthly_usage(user_id)

    plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    limit = plan_limits["tokens_per_month"]

    if limit is None:
        # Unlimited plan (enterprise)
        percentage_used = 0.0
    else:
        percentage_used = (current_usage / limit) * 100 if limit > 0 else 0.0

    return QuotaStatus(
        user_email=user_email,
        plan=plan,
        current_usage=current_usage,
        limit=limit,
        percentage_used=percentage_used,
    )


def get_days_until_reset() -> int:
    """
    Get days until quota resets (next month 1st)

    Returns:
        Number of days until reset
    """
    now = datetime.utcnow()

    # Calculate first day of next month
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)

    days_remaining = (next_month - now).days
    return days_remaining


def check_instance_limit(user_email: str, plan: str = "free") -> bool:
    """
    Check if user can create another instance

    Args:
        user_email: User email address
        plan: User's plan

    Returns:
        True if user can create another instance, False otherwise
    """
    from app.utils.user_id import generate_user_id
    from kubernetes import client

    plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_instances = plan_limits["max_instances"]

    # Enterprise has unlimited instances
    if max_instances is None:
        return True

    # Check current instance count
    user_id = generate_user_id(user_email)
    namespace = f"openclaw-{user_id}"

    try:
        k8s_custom = client.CustomObjectsApi()

        instances = k8s_custom.list_namespaced_custom_object(
            group="openclaw.rocks",
            version="v1alpha1",
            namespace=namespace,
            plural="openclawinstances"
        )

        current_count = len(instances.get('items', []))

        return current_count < max_instances

    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Namespace doesn't exist, user can create first instance
            return True
        else:
            logger.error(f"Failed to check instance limit for {user_email}: {e}")
            return False
    except Exception as e:
        logger.error(f"Failed to check instance limit for {user_email}: {e}")
        return False
