"""Admin API endpoints"""
from flask import Blueprint, jsonify, session
import logging
from kubernetes import client
from datetime import datetime, timezone

from app.utils.session_auth import require_auth, require_admin
from app.utils.user_id import generate_user_id
from app.database import get_all_users_with_usage, get_db_connection

logger = logging.getLogger(__name__)


def _parse_cpu(value):
    """Parse K8s CPU value to millicores. '250m' → 250, '2' → 2000, '100n' → 0."""
    if not value or value == '?':
        return None
    s = str(value).strip()
    if s.endswith('n'):
        return int(s[:-1]) / 1_000_000
    if s.endswith('m'):
        return int(s[:-1])
    try:
        return int(float(s) * 1000)
    except (ValueError, TypeError):
        return None


def _parse_memory(value):
    """Parse K8s memory value to bytes. '1234Ki' → bytes, '1Gi' → bytes."""
    if not value or value == '?':
        return None
    s = str(value).strip()
    multipliers = {
        'Ki': 1024, 'Mi': 1024 ** 2, 'Gi': 1024 ** 3, 'Ti': 1024 ** 4,
        'k': 1000, 'M': 1000 ** 2, 'G': 1000 ** 3, 'T': 1000 ** 4,
    }
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return int(s[:-len(suffix)]) * mult
            except (ValueError, TypeError):
                return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None

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


def _format_age(creation_timestamp):
    """Format age from creationTimestamp to 'Xd Xh Xm' string."""
    if not creation_timestamp:
        return "unknown"
    try:
        if isinstance(creation_timestamp, str):
            created = datetime.fromisoformat(creation_timestamp.replace('Z', '+00:00'))
        else:
            created = creation_timestamp
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - created
        total_minutes = int(delta.total_seconds() / 60)
        days = total_minutes // (60 * 24)
        hours = (total_minutes % (60 * 24)) // 60
        minutes = total_minutes % 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
    except Exception:
        return "unknown"


@admin_bp.route('/admin/cluster', methods=['GET'])
@require_auth
@require_admin
def get_cluster_overview():
    """
    Get cluster-wide overview including instances, nodes, pods, and Karpenter status (admin only).
    """
    try:
        k8s_core = client.CoreV1Api()
        k8s_custom = client.CustomObjectsApi()

        # --- Instances ---
        try:
            instances_resp = k8s_custom.list_cluster_custom_object(
                group="openclaw.rocks",
                version="v1alpha1",
                plural="openclawinstances"
            )
            all_instances = instances_resp.get('items', [])
        except Exception as e:
            logger.warning(f"Failed to list OpenClawInstances: {e}")
            all_instances = []

        instances_total = len(all_instances)
        instances_running = 0
        instances_pending = 0
        instances_failed = 0
        for inst in all_instances:
            phase = inst.get('status', {}).get('phase', 'Unknown')
            if phase == 'Running':
                instances_running += 1
            elif phase in ('Pending', 'Creating', 'Provisioning'):
                instances_pending += 1
            elif phase in ('Failed', 'Error'):
                instances_failed += 1

        instances_data = {
            "total": instances_total,
            "running": instances_running,
            "pending": instances_pending,
            "failed": instances_failed,
        }

        # --- Nodes ---
        try:
            nodes_resp = k8s_core.list_node()
            all_nodes = nodes_resp.items
        except Exception as e:
            logger.warning(f"Failed to list nodes: {e}")
            all_nodes = []

        # Count pods per node
        try:
            all_pods_resp = k8s_core.list_pod_for_all_namespaces()
            all_pods = all_pods_resp.items
        except Exception as e:
            logger.warning(f"Failed to list pods: {e}")
            all_pods = []

        pods_per_node = {}
        for pod in all_pods:
            node_name = pod.spec.node_name
            if node_name:
                pods_per_node[node_name] = pods_per_node.get(node_name, 0) + 1

        # Fetch node metrics (CPU/Memory usage) from metrics-server
        node_metrics = {}
        try:
            metrics_resp = k8s_custom.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes"
            )
            for item in metrics_resp.get('items', []):
                name = item.get('metadata', {}).get('name', '')
                usage = item.get('usage', {})
                node_metrics[name] = {
                    'cpu': usage.get('cpu', ''),
                    'memory': usage.get('memory', ''),
                }
        except Exception as e:
            logger.warning(f"Failed to fetch node metrics (metrics-server may not be installed): {e}")

        node_details = []
        karpenter_managed_count = 0
        system_count = 0

        for node in all_nodes:
            labels = node.metadata.labels or {}
            name = node.metadata.name
            instance_type = labels.get('node.kubernetes.io/instance-type', labels.get('beta.kubernetes.io/instance-type', 'unknown'))
            is_karpenter = 'karpenter.sh/nodepool' in labels
            managed_by = 'karpenter' if is_karpenter else 'system'

            if is_karpenter:
                karpenter_managed_count += 1
            else:
                system_count += 1

            # Node status
            node_status = "Unknown"
            if node.status and node.status.conditions:
                for cond in node.status.conditions:
                    if cond.type == "Ready":
                        node_status = "Ready" if cond.status == "True" else "NotReady"
                        break

            # Capacity & allocatable
            capacity = node.status.capacity or {} if node.status else {}
            allocatable = node.status.allocatable or {} if node.status else {}

            age = _format_age(node.metadata.creation_timestamp)

            # Calculate CPU/Memory usage percentages from metrics-server
            cpu_percent = None
            memory_percent = None
            metrics = node_metrics.get(name, {})
            if metrics:
                usage_cpu_m = _parse_cpu(metrics.get('cpu', ''))
                alloc_cpu_m = _parse_cpu(allocatable.get('cpu', ''))
                if usage_cpu_m is not None and alloc_cpu_m and alloc_cpu_m > 0:
                    cpu_percent = round(usage_cpu_m / alloc_cpu_m * 100, 1)

                usage_mem_b = _parse_memory(metrics.get('memory', ''))
                alloc_mem_b = _parse_memory(allocatable.get('memory', ''))
                if usage_mem_b is not None and alloc_mem_b and alloc_mem_b > 0:
                    memory_percent = round(usage_mem_b / alloc_mem_b * 100, 1)

            node_details.append({
                "name": name,
                "instance_type": instance_type,
                "capacity": {
                    "cpu": str(capacity.get('cpu', '?')),
                    "memory": str(capacity.get('memory', '?')),
                },
                "allocatable": {
                    "cpu": str(allocatable.get('cpu', '?')),
                    "memory": str(allocatable.get('memory', '?')),
                },
                "pods_count": pods_per_node.get(name, 0),
                "managed_by": managed_by,
                "status": node_status,
                "age": age,
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
            })

        nodes_data = {
            "total": len(all_nodes),
            "karpenter_managed": karpenter_managed_count,
            "system": system_count,
            "details": node_details,
        }

        # --- Pods ---
        # Only count pods in actual agent instance namespaces (openclaw-<hash>),
        # excluding system namespaces like openclaw-operator-system and openclaw-provisioning
        system_ns = {'openclaw-operator-system', 'openclaw-provisioning'}
        pods_by_namespace = {}
        openclaw_pods = 0
        for pod in all_pods:
            ns = pod.metadata.namespace
            pods_by_namespace[ns] = pods_by_namespace.get(ns, 0) + 1
            if ns and ns.startswith('openclaw-') and ns not in system_ns:
                openclaw_pods += 1

        pods_data = {
            "total": len(all_pods),
            "by_namespace": pods_by_namespace,
            "openclaw_pods": openclaw_pods,
        }

        # --- Karpenter ---
        # NodeClaims
        nodeclaims_list = []
        try:
            nc_resp = k8s_custom.list_cluster_custom_object(
                group="karpenter.sh",
                version="v1",
                plural="nodeclaims"
            )
            for nc in nc_resp.get('items', []):
                nc_status = nc.get('status', {})
                nc_conditions = nc_status.get('conditions', [])
                nc_phase = 'Unknown'
                for cond in nc_conditions:
                    if cond.get('type') == 'Ready':
                        nc_phase = 'Ready' if cond.get('status') == 'True' else 'NotReady'
                        break

                # Instance type from status or spec
                nc_instance_type = nc_status.get('instanceType', '')
                if not nc_instance_type:
                    reqs = nc.get('spec', {}).get('requirements', [])
                    for req in reqs:
                        if req.get('key') == 'node.kubernetes.io/instance-type':
                            vals = req.get('values', [])
                            if vals:
                                nc_instance_type = vals[0]
                            break

                nodeclaims_list.append({
                    "name": nc.get('metadata', {}).get('name', ''),
                    "instance_type": nc_instance_type,
                    "status": nc_phase,
                    "created_at": nc.get('metadata', {}).get('creationTimestamp', ''),
                })
        except client.exceptions.ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to list NodeClaims: {e}")
        except Exception as e:
            logger.warning(f"Failed to list NodeClaims: {e}")

        # Karpenter events (last 2 hours) — only actual scaling actions
        # Filter out periodic check noise (Unconsolidatable, etc.)
        recent_events = []
        try:
            events_resp = k8s_core.list_event_for_all_namespaces()
            cutoff = datetime.now(timezone.utc).timestamp() - 7200  # 2 hours ago
            # Only reasons that indicate actual scaling actions
            scaling_reasons = {
                # Scale up
                'Nominated', 'Created', 'Launched', 'Registered', 'Initialized',
                # Scale down
                'DisruptionTerminating', 'Disrupting', 'Terminating', 'Finalized',
                'Drifted', 'Expired',
                # Errors during scaling
                'FailedScheduling', 'FailedLaunching', 'TerminatingOnDeletion',
            }
            karpenter_kinds = {'NodeClaim', 'NodePool', 'Machine', 'Provisioner'}
            for event in events_resp.items:
                source = event.source
                involved = event.involved_object
                is_karpenter_source = (
                    source and source.component and 'karpenter' in source.component.lower()
                )
                is_karpenter_object = (
                    involved and involved.kind in karpenter_kinds
                )
                reporting = getattr(event, 'reporting_component', '') or ''
                is_karpenter_reporting = 'karpenter' in reporting.lower()

                if not (is_karpenter_source or is_karpenter_object or is_karpenter_reporting):
                    continue

                # Filter to scaling-related reasons only
                reason = event.reason or ''
                if reason not in scaling_reasons:
                    continue

                event_time = event.last_timestamp or event.event_time or event.metadata.creation_timestamp
                if event_time:
                    if hasattr(event_time, 'timestamp'):
                        ts = event_time.timestamp()
                    else:
                        ts = datetime.fromisoformat(str(event_time).replace('Z', '+00:00')).timestamp()
                    if ts >= cutoff:
                        recent_events.append({
                            "type": event.type or "Normal",
                            "reason": reason,
                            "message": event.message or "",
                            "timestamp": event_time.isoformat() if hasattr(event_time, 'isoformat') else str(event_time),
                        })
            # Sort by timestamp desc, limit to 30
            recent_events.sort(key=lambda e: e['timestamp'], reverse=True)
            recent_events = recent_events[:30]
        except Exception as e:
            logger.warning(f"Failed to list Karpenter events: {e}")

        karpenter_data = {
            "nodeclaims": nodeclaims_list,
            "recent_events": recent_events,
        }

        return jsonify({
            "instances": instances_data,
            "nodes": nodes_data,
            "pods": pods_data,
            "karpenter": karpenter_data,
        }), 200

    except Exception as e:
        logger.error(f"Failed to get cluster overview: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Failed to fetch cluster overview"}), 500
