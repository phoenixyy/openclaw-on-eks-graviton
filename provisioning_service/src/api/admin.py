"""Admin API endpoints"""
from flask import Blueprint, jsonify, session
import logging
from kubernetes import client
from datetime import datetime

from app.utils.session_auth import require_auth, require_admin
from app.utils.user_id import generate_user_id
from app.database import get_all_users_with_usage, get_db_connection

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/admin/users', methods=['GET'])
@require_auth
@require_admin
def list_all_users():
    """
    List all users with their instances and usage stats (admin only)

    Returns:
        {
            "users": [
                {
                    "user_id": "7ec7606c",
                    "email": "user@example.com",
                    "username": "johndoe",
                    "created_at": "2026-03-10T10:00:00Z",
                    "instance": {
                        "status": "Running",
                        "runtime": "kata-qemu",
                        "provider": "bedrock",
                        "created_at": "2026-03-12T08:00:00Z",
                        "usage_30d": {
                            "total_tokens": 123456,
                            "estimated_cost": 1.23
                        }
                    }
                }
            ],
            "summary": {
                "total_users": 10,
                "active_instances": 8,
                "total_tokens_30d": 9876543,
                "total_cost_30d": 98.76
            }
        }
    """
    try:
        # Get all users with usage
        users = get_all_users_with_usage(days=30)

        # Kubernetes API client
        k8s_core = client.CoreV1Api()
        k8s_custom = client.CustomObjectsApi()

        # Enrich with instance information
        enriched_users = []
        total_tokens = 0
        total_cost = 0.0
        active_instances = 0

        for user in users:
            user_id = generate_user_id(user['email'])
            namespace = f"openclaw-{user_id}"

            # Check if OpenClawInstance exists
            instance_data = None
            try:
                # Get OpenClawInstance CRD
                instance = k8s_custom.get_namespaced_custom_object(
                    group="openclaw.rocks",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="openclawinstances",
                    name=f"openclaw-{user_id}"
                )

                # Extract instance info
                spec = instance.get('spec', {})
                status = instance.get('status', {})

                # Get runtime class
                runtime = spec.get('availability', {}).get('runtimeClassName', 'runc')

                # Determine provider from model
                model = spec.get('config', {}).get('raw', {}).get('agents', {}).get('defaults', {}).get('model', {}).get('primary', '')
                provider = 'bedrock' if 'bedrock' in model.lower() else 'siliconflow' if 'siliconflow' in model.lower() else 'unknown'

                instance_data = {
                    'status': status.get('phase', 'Unknown'),
                    'runtime': runtime,
                    'provider': provider,
                    'created_at': instance.get('metadata', {}).get('creationTimestamp', ''),
                    'usage_30d': user['usage_30d']
                }

                if status.get('phase') == 'Running':
                    active_instances += 1

            except client.exceptions.ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to get instance for {user_id}: {e}")
                # No instance for this user
                instance_data = None

            # Add to enriched list
            enriched_users.append({
                'user_id': user_id,
                'email': user['email'],
                'username': user['username'],
                'created_at': user['created_at'],
                'instance': instance_data
            })

            # Accumulate totals
            total_tokens += user['usage_30d']['total_tokens']
            total_cost += user['usage_30d']['estimated_cost']

        # Build summary
        summary = {
            'total_users': len(enriched_users),
            'active_instances': active_instances,
            'total_tokens_30d': total_tokens,
            'total_cost_30d': round(total_cost, 2)
        }

        return jsonify({
            'users': enriched_users,
            'summary': summary
        }), 200

    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Failed to fetch user list"}), 500


@admin_bp.route('/admin/usage/summary', methods=['GET'])
@require_auth
@require_admin
def get_platform_usage():
    """
    Get platform-wide usage statistics (admin only)

    Returns:
        {
            "total_tokens": 9876543,
            "total_cost": 98.76,
            "total_calls": 50000,
            "by_provider": [
                {"provider": "bedrock", "total_tokens": 8000000, "estimated_cost": 80.0},
                {"provider": "siliconflow", "total_tokens": 1876543, "estimated_cost": 18.76}
            ],
            "by_model": [
                {"model": "claude-opus-4-6", "total_tokens": 5000000, "estimated_cost": 50.0},
                ...
            ],
            "daily": [
                {"date": "2026-03-14", "total_tokens": 500000, "estimated_cost": 5.0},
                ...
            ]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Overall summary (last 30 days)
        cursor.execute('''
            SELECT
                SUM(total_tokens) as total_tokens,
                SUM(estimated_cost) as total_cost,
                SUM(call_count) as total_calls
            FROM daily_usage
            WHERE date >= date('now', '-30 days')
        ''')

        summary_row = cursor.fetchone()
        summary = {
            'total_tokens': summary_row[0] or 0,
            'total_cost': round(summary_row[1] or 0.0, 2),
            'total_calls': summary_row[2] or 0
        }

        # By provider
        cursor.execute('''
            SELECT
                provider,
                SUM(total_tokens) as total_tokens,
                SUM(estimated_cost) as estimated_cost
            FROM daily_usage
            WHERE date >= date('now', '-30 days')
            GROUP BY provider
            ORDER BY total_tokens DESC
        ''')

        by_provider = [
            {
                'provider': row[0],
                'total_tokens': row[1],
                'estimated_cost': round(row[2], 2)
            }
            for row in cursor.fetchall()
        ]

        # By model
        cursor.execute('''
            SELECT
                model,
                SUM(total_tokens) as total_tokens,
                SUM(estimated_cost) as estimated_cost
            FROM daily_usage
            WHERE date >= date('now', '-30 days')
            GROUP BY model
            ORDER BY total_tokens DESC
            LIMIT 10
        ''')

        by_model = [
            {
                'model': row[0],
                'total_tokens': row[1],
                'estimated_cost': round(row[2], 2)
            }
            for row in cursor.fetchall()
        ]

        # Daily breakdown
        cursor.execute('''
            SELECT
                date,
                SUM(total_tokens) as total_tokens,
                SUM(estimated_cost) as estimated_cost
            FROM daily_usage
            WHERE date >= date('now', '-30 days')
            GROUP BY date
            ORDER BY date ASC
        ''')

        daily = [
            {
                'date': row[0],
                'total_tokens': row[1],
                'estimated_cost': round(row[2], 2)
            }
            for row in cursor.fetchall()
        ]

        conn.close()

        return jsonify({
            **summary,
            'by_provider': by_provider,
            'by_model': by_model,
            'daily': daily
        }), 200

    except Exception as e:
        logger.error(f"Failed to get platform usage: {e}")
        return jsonify({"error": "Failed to fetch platform usage"}), 500
