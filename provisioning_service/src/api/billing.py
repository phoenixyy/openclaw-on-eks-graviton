"""Billing API endpoints"""
from flask import Blueprint, jsonify, request, session
import logging

from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
from app.database import get_user_usage_summary, get_db_connection, get_user_by_email
from app.services.quota import PLAN_LIMITS, check_quota, get_days_until_reset

logger = logging.getLogger(__name__)

billing_bp = Blueprint('billing', __name__)


@billing_bp.route('/billing/plans', methods=['GET'])
def list_plans():
    """
    List available plans and their limits (public endpoint)

    Returns:
        {
            "plans": {
                "free": {
                    "tokens_per_month": 100000,
                    "max_instances": 1,
                    "price_monthly": 0,
                    "features": [...]
                },
                ...
            }
        }
    """
    return jsonify({"plans": PLAN_LIMITS}), 200


@billing_bp.route('/billing/usage', methods=['GET'])
@require_auth
def get_my_usage():
    """
    Get current user's usage statistics with quota info

    Query params:
        days: Number of days to look back (default: 30)

    Returns:
        {
            "period_days": 30,
            "plan": "free",
            "quota": {
                "user_email": "user@example.com",
                "plan": "free",
                "current_usage": 85000,
                "limit": 100000,
                "percentage_used": 85.0,
                "is_warning": true,
                "is_over_quota": false,
                "status_emoji": "🟡",
                "status_text": "Warning"
            },
            "days_until_reset": 16,
            "summary": {
                "total_tokens": 1234567,
                "input_tokens": 800000,
                "output_tokens": 434567,
                "total_calls": 150,
                "estimated_cost": 12.34
            },
            "by_model": [...],
            "daily": [...]
        }
    """
    try:
        # Get query params
        days = request.args.get('days', 30, type=int)
        if days < 1 or days > 365:
            return jsonify({"error": "Invalid days parameter (must be 1-365)"}), 400

        # Get user info
        user_email = session['user_email']
        user_id = generate_user_id(user_email)

        # Get user's plan
        user = get_user_by_email(user_email)
        plan = user.get('plan', 'free') if user else 'free'

        # Get usage summary
        usage_data = get_user_usage_summary(user_id, days)

        # Check quota
        quota_status = check_quota(user_email, plan)

        return jsonify({
            "period_days": days,
            "plan": plan,
            "quota": quota_status.to_dict(),
            "days_until_reset": get_days_until_reset(),
            **usage_data
        }), 200

    except Exception as e:
        logger.error(f"Failed to get user usage: {e}")
        return jsonify({"error": "Failed to fetch usage data"}), 500


@billing_bp.route('/billing/quota', methods=['GET'])
@require_auth
def get_quota_status():
    """
    Get current quota status for logged-in user

    Returns:
        {
            "user_email": "user@example.com",
            "plan": "free",
            "current_usage": 85000,
            "limit": 100000,
            "percentage_used": 85.0,
            "is_warning": true,
            "is_over_quota": false,
            "status_emoji": "🟡",
            "status_text": "Warning"
        }
    """
    try:
        user_email = session['user_email']
        user = get_user_by_email(user_email)
        plan = user.get('plan', 'free') if user else 'free'

        quota_status = check_quota(user_email, plan)

        return jsonify(quota_status.to_dict()), 200

    except Exception as e:
        logger.error(f"Failed to get quota status: {e}")
        return jsonify({"error": "Failed to fetch quota status"}), 500


@billing_bp.route('/billing/upgrade', methods=['POST'])
@require_auth
def upgrade_plan():
    """
    Upgrade user's plan (MVP: direct upgrade without payment)

    Request body:
        {
            "plan": "pro"
        }

    Returns:
        {
            "user_email": "user@example.com",
            "old_plan": "free",
            "new_plan": "pro",
            "limits": {...},
            "message": "Plan upgraded successfully"
        }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body is required"}), 400

        new_plan = data.get('plan')

        if not new_plan or new_plan not in PLAN_LIMITS:
            return jsonify({"error": f"Invalid plan: {new_plan}. Must be one of: {list(PLAN_LIMITS.keys())}"}), 400

        user_email = session['user_email']
        user = get_user_by_email(user_email)

        if not user:
            return jsonify({"error": "User not found"}), 404

        old_plan = user.get('plan', 'free')

        # Don't allow downgrade to free (optional business rule)
        if old_plan == 'pro' and new_plan == 'free':
            return jsonify({"error": "Cannot downgrade from pro to free. Please contact support."}), 400

        # Update plan in database
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users
            SET plan = %s, updated_at = CURRENT_TIMESTAMP
            WHERE email = %s
        ''', (new_plan, user_email))

        conn.commit()
        conn.close()

        logger.info(f"✅ Plan upgraded: {user_email} from {old_plan} to {new_plan}")

        return jsonify({
            "user_email": user_email,
            "old_plan": old_plan,
            "new_plan": new_plan,
            "limits": PLAN_LIMITS[new_plan],
            "message": f"Plan upgraded from {old_plan} to {new_plan}"
        }), 200

    except Exception as e:
        logger.error(f"Failed to upgrade plan: {e}")
        return jsonify({"error": "Failed to upgrade plan"}), 500


@billing_bp.route('/billing/hourly', methods=['GET'])
@require_auth
def get_hourly_usage():
    """
    Get hourly usage time series for current user (from usage_events)

    Query params:
        hours: Number of hours to look back (default: 24)

    Returns:
        {
            "period_hours": 24,
            "data": [
                {
                    "hour": "2026-03-14 15:00:00",
                    "total_tokens": 12345,
                    "estimated_cost": 0.15,
                    "call_count": 10
                }
            ]
        }
    """
    try:
        hours = request.args.get('hours', 24, type=int)
        if hours < 1 or hours > 168:
            return jsonify({"error": "Invalid hours parameter (must be 1-168)"}), 400

        user_email = session['user_email']
        user_id = generate_user_id(user_email)

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                DATE_TRUNC('hour', timestamp) as hour,
                SUM(total_tokens) as total_tokens,
                SUM(cost_usd) as total_cost,
                COUNT(*) as call_count
            FROM usage_events
            WHERE tenant_id = %s AND timestamp >= NOW() - INTERVAL '%s hours'
            GROUP BY DATE_TRUNC('hour', timestamp)
            ORDER BY hour ASC
        ''', (user_id, hours))

        hourly_data = [
            {
                'hour': str(row[0]),
                'total_tokens': int(row[1]),
                'estimated_cost': round(float(row[2]), 4),
                'call_count': int(row[3])
            }
            for row in cursor.fetchall()
        ]

        cursor.close()
        conn.close()

        return jsonify({
            "period_hours": hours,
            "data": hourly_data
        }), 200

    except Exception as e:
        logger.error(f"Failed to get hourly usage: {e}")
        return jsonify({"error": "Failed to fetch hourly usage data"}), 500
