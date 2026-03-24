"""Status API endpoint"""
from flask import Blueprint, jsonify, session
from app.k8s.client import K8sClient
from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
from app.database import get_user_instances, get_instance_by_id
from kubernetes.client.rest import ApiException
import logging

status_bp = Blueprint('status', __name__)
logger = logging.getLogger(__name__)

@status_bp.route('/status/<identifier>', methods=['GET'])
@require_auth
def status(identifier):
    """
    Get OpenClaw instance status

    Authentication: Requires valid session (user must be logged in)
    Authorization: Users can only access their own instances

    Args:
        identifier: Instance ID (format: user_id-seq) or User ID (backward compatibility)

    Response (200 OK):
    {
        "instance_id": "7ec7606c-01",
        "user_id": "7ec7606c",
        "namespace": "openclaw-7ec7606c-01",
        "instance_name": "openclaw-7ec7606c-01",
        "status": "Running",
        ...
    }

    Response (403 Forbidden):
    {
        "error": "Forbidden: You can only access your own instances"
    }

    Response (404 Not Found):
    {
        "error": "Instance not found"
    }
    """
    try:
        # Get user email from session
        user_email = session['user_email']
        authenticated_user_id = generate_user_id(user_email)

        # Determine if identifier is instance_id or user_id
        if '-' in identifier:
            # This is an instance_id (format: user_id-seq)
            instance_id = identifier
            user_id = instance_id.split('-')[0]

            # Verify user owns this instance
            if user_id != authenticated_user_id:
                logger.warning(f"⚠️ Unauthorized access attempt: {user_email} tried to access instance {instance_id}")
                return jsonify({
                    "error": "Forbidden: You can only access your own instances"
                }), 403
        else:
            # Backward compatibility: treat as user_id, return first instance
            user_id = identifier

            # Verify user can only access their own instances
            if user_id != authenticated_user_id:
                logger.warning(f"⚠️ Unauthorized access attempt: {user_email} tried to access user_id {user_id}")
                return jsonify({
                    "error": "Forbidden: You can only access your own instances"
                }), 403

            # Get first instance for this user
            instances = get_user_instances(user_email)
            if not instances:
                return jsonify({"error": "Instance not found"}), 404

            # Use the first instance (for backward compatibility)
            instance_id = instances[0]['instance_id']

        namespace = f"openclaw-{instance_id}"
        instance_name = f"openclaw-{instance_id}"

        k8s_client = K8sClient()

        # Get OpenClawInstance CRD
        instance = k8s_client.custom_objects.get_namespaced_custom_object(
            group="openclaw.rocks",
            version="v1alpha1",
            namespace=namespace,
            plural="openclawinstances",
            name=instance_name
        )

        # Get Pod status with detailed readiness checks
        pod_status = []
        pods_ready = False
        try:
            pods = k8s_client.core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app.kubernetes.io/instance={instance_name}"
            )
            for pod in pods.items:
                containers_ready = False
                if pod.status.container_statuses:
                    containers_ready = all(cs.ready for cs in pod.status.container_statuses)

                pod_status.append({
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "ready": containers_ready,
                    "containers": len(pod.status.container_statuses) if pod.status.container_statuses else 0,
                    "containers_ready": sum(1 for cs in pod.status.container_statuses if cs.ready) if pod.status.container_statuses else 0
                })

            # Pods are ready if at least one pod is Running and all containers are ready
            pods_ready = any(
                pod.status.phase == "Running" and
                pod.status.container_statuses and
                all(cs.ready for cs in pod.status.container_statuses)
                for pod in pods.items
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not read pod status: {str(e)}")
            pod_status = []

        # Extract status from OpenClawInstance
        instance_status = instance.get('status', {})
        phase = instance_status.get('phase', 'Pending')
        gateway_endpoint = instance_status.get('gatewayEndpoint', '')

        # Get creation timestamp
        created_at = instance.get('metadata', {}).get('creationTimestamp', '')

        # Get LLM provider from label
        llm_provider = instance.get('metadata', {}).get('labels', {}).get('openclaw.rocks/llm-provider', 'bedrock')

        # Check Service endpoints readiness
        service_ready = False
        try:
            endpoints = k8s_client.core_v1.read_namespaced_endpoints(
                name=instance_name,
                namespace=namespace
            )
            # Service is ready if it has at least one ready endpoint
            service_ready = bool(
                endpoints.subsets and
                any(subset.addresses for subset in endpoints.subsets)
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not read service endpoints: {str(e)}")

        # Get gateway token from Secret (needed for accessing OpenClaw gateway)
        gateway_token = None
        gateway_token_exists = False
        try:
            import base64
            secret = k8s_client.core_v1.read_namespaced_secret(
                name=f"{instance_name}-gateway-token",
                namespace=namespace
            )
            gateway_token = base64.b64decode(secret.data.get('token', '')).decode('utf-8')
            gateway_token_exists = bool(gateway_token)
        except Exception as e:
            logger.warning(f"⚠️ Could not read gateway token: {str(e)}")

        # Determine overall readiness for connection
        # All conditions must be met: phase=Running, pods ready, service ready, token exists
        ready_for_connect = (
            phase == 'Running' and
            pods_ready and
            service_ready and
            gateway_token_exists
        )

        # Generate detailed status message for UI
        if phase != 'Running':
            status_message = f"Instance is {phase}"
        elif not pods_ready:
            if not pod_status:
                status_message = "Waiting for pods to start..."
            else:
                ready_containers = pod_status[0].get('containers_ready', 0)
                total_containers = pod_status[0].get('containers', 0)
                status_message = f"Starting containers ({ready_containers}/{total_containers} ready)..."
        elif not service_ready:
            status_message = "Waiting for service endpoints..."
        elif not gateway_token_exists:
            status_message = "Waiting for gateway token..."
        else:
            status_message = "Ready"

        # Build API Gateway URL and CloudFront URLs for external access (with gateway token)
        from app.config import Config
        api_gateway_url = None
        cloudfront_url = None
        cloudfront_http_url = None

        if Config.INGRESS_ENABLED and ready_for_connect and gateway_token:
            # Legacy API Gateway URL (keep for backward compatibility)
            api_gateway_url = f"{Config.API_GATEWAY_ENDPOINT}/{Config.API_GATEWAY_STAGE}/instance/{user_id}/?token={gateway_token}"

        if Config.USE_PUBLIC_ALB and ready_for_connect and gateway_token:
            # CloudFront WebSocket URL (primary for new frontend)
            cloudfront_url = f"wss://{Config.CLOUDFRONT_DOMAIN}/instance/{user_id}?token={gateway_token}"
            # CloudFront HTTP URL (for display in UI)
            cloudfront_http_url = f"https://{Config.CLOUDFRONT_DOMAIN}/instance/{user_id}/?token={gateway_token}"

        response = {
            "instance_id": instance_id,
            "user_id": user_id,
            "namespace": namespace,
            "instance_name": instance_name,
            "status": phase,  # Simple string: "Running", "Pending", etc.
            "ready_for_connect": ready_for_connect,  # Boolean: true when safe to connect
            "status_message": status_message,  # Human-readable status for UI
            "gateway_endpoint": gateway_endpoint,  # Internal cluster endpoint
            "api_gateway_url": api_gateway_url,  # External API Gateway URL (with token) - legacy
            "cloudfront_url": cloudfront_url,  # CloudFront WebSocket URL (wss://) - primary
            "cloudfront_http_url": cloudfront_http_url,  # CloudFront HTTP URL (https://) - for display
            "gateway_token": gateway_token if ready_for_connect else None,  # Only expose token when ready
            "created_at": created_at,
            "llm_provider": llm_provider,  # 'bedrock' or 'siliconflow'
            "pods": pod_status,
            "readiness_checks": {  # Detailed readiness info for debugging
                "phase_running": phase == 'Running',
                "pods_ready": pods_ready,
                "service_ready": service_ready,
                "gateway_token_exists": gateway_token_exists
            },
            "raw_status": instance_status  # Keep full status for debugging
        }

        return jsonify(response), 200

    except ApiException as e:
        if e.status == 404:
            return jsonify({"error": "Instance not found"}), 404
        logger.error(f"❌ Error getting status: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"❌ Error getting status: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@status_bp.route('/instances', methods=['GET'])
@require_auth
def list_instances():
    """
    List all OpenClaw instances for the current user

    Authentication: Requires valid session (user must be logged in)

    Response (200 OK):
    {
        "instances": [
            {
                "instance_id": "7ec7606c-01",
                "user_id": "7ec7606c",
                "display_name": "My First Instance",
                "namespace": "openclaw-7ec7606c-01",
                "status": "Running",
                "created_at": "2024-03-24T10:00:00Z",
                ...
            },
            ...
        ]
    }
    """
    try:
        # Get user email from session
        user_email = session['user_email']

        # Get all instances from database
        db_instances = get_user_instances(user_email)

        if not db_instances:
            return jsonify({"instances": []}), 200

        # For each instance, get K8s status
        instances = []
        k8s_client = K8sClient()

        for db_instance in db_instances:
            instance_id = db_instance['instance_id']
            namespace = f"openclaw-{instance_id}"
            instance_name = f"openclaw-{instance_id}"

            try:
                # Get OpenClawInstance CRD
                instance = k8s_client.custom_objects.get_namespaced_custom_object(
                    group="openclaw.rocks",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="openclawinstances",
                    name=instance_name
                )

                # Extract status
                instance_status = instance.get('status', {})
                phase = instance_status.get('phase', 'Pending')
                gateway_endpoint = instance_status.get('gatewayEndpoint', '')
                created_at = instance.get('metadata', {}).get('creationTimestamp', '')
                llm_provider = instance.get('metadata', {}).get('labels', {}).get('openclaw.rocks/llm-provider', 'bedrock')

                # Get Pod status
                pods_ready = False
                try:
                    pods = k8s_client.core_v1.list_namespaced_pod(
                        namespace=namespace,
                        label_selector=f"app.kubernetes.io/instance={instance_name}"
                    )
                    pods_ready = any(
                        pod.status.phase == "Running" and
                        pod.status.container_statuses and
                        all(cs.ready for cs in pod.status.container_statuses)
                        for pod in pods.items
                    )
                except Exception:
                    pass

                # Check Service readiness
                service_ready = False
                try:
                    endpoints = k8s_client.core_v1.read_namespaced_endpoints(
                        name=instance_name,
                        namespace=namespace
                    )
                    service_ready = bool(
                        endpoints.subsets and
                        any(subset.addresses for subset in endpoints.subsets)
                    )
                except Exception:
                    pass

                # Check gateway token
                gateway_token_exists = False
                gateway_token = None
                try:
                    import base64
                    secret = k8s_client.core_v1.read_namespaced_secret(
                        name=f"{instance_name}-gateway-token",
                        namespace=namespace
                    )
                    gateway_token = base64.b64decode(secret.data.get('token', '')).decode('utf-8')
                    gateway_token_exists = bool(gateway_token)
                except Exception:
                    pass

                # Determine overall readiness
                ready_for_connect = (
                    phase == 'Running' and
                    pods_ready and
                    service_ready and
                    gateway_token_exists
                )

                # Generate status message
                if phase != 'Running':
                    status_message = f"{phase}"
                elif not pods_ready:
                    status_message = "Starting..."
                elif not service_ready:
                    status_message = "Waiting for service..."
                elif not gateway_token_exists:
                    status_message = "Waiting for token..."
                else:
                    status_message = "Ready"

                # Build CloudFront URLs
                from app.config import Config
                cloudfront_url = None
                cloudfront_http_url = None
                if Config.USE_PUBLIC_ALB and ready_for_connect and gateway_token:
                    cloudfront_url = f"wss://{Config.CLOUDFRONT_DOMAIN}/instance/{instance_id}?token={gateway_token}"
                    cloudfront_http_url = f"https://{Config.CLOUDFRONT_DOMAIN}/instance/{instance_id}/?token={gateway_token}"

                instances.append({
                    "instance_id": instance_id,
                    "user_id": db_instance['user_id'],
                    "display_name": db_instance['display_name'],
                    "namespace": namespace,
                    "instance_name": instance_name,
                    "status": phase,
                    "status_message": status_message,
                    "ready_for_connect": ready_for_connect,
                    "gateway_endpoint": gateway_endpoint,
                    "cloudfront_url": cloudfront_url,
                    "cloudfront_http_url": cloudfront_http_url,
                    "gateway_token": gateway_token if ready_for_connect else None,
                    "created_at": created_at or db_instance['created_at'].isoformat(),
                    "llm_provider": llm_provider,
                    "model": db_instance.get('model')
                })

            except ApiException as e:
                if e.status == 404:
                    # Instance exists in DB but not in K8s (being created or deleted)
                    instances.append({
                        "instance_id": instance_id,
                        "user_id": db_instance['user_id'],
                        "display_name": db_instance['display_name'],
                        "namespace": namespace,
                        "status": "Pending",
                        "status_message": "Creating...",
                        "ready_for_connect": False,
                        "created_at": db_instance['created_at'].isoformat(),
                        "llm_provider": db_instance['provider'],
                        "model": db_instance.get('model')
                    })
                else:
                    logger.error(f"❌ Error getting instance {instance_id}: {str(e)}")

        return jsonify({"instances": instances}), 200

    except Exception as e:
        logger.error(f"❌ Error listing instances: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
