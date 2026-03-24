"""Status API endpoint"""
from flask import Blueprint, jsonify, session
from app.k8s.client import K8sClient
from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
from kubernetes.client.rest import ApiException
import logging

status_bp = Blueprint('status', __name__)
logger = logging.getLogger(__name__)

@status_bp.route('/status/<user_id>', methods=['GET'])
@require_auth
def status(user_id):
    """
    Get OpenClaw instance status

    Authentication: Requires valid session (user must be logged in)
    Authorization: Users can only access their own instances

    Args:
        user_id: User ID

    Response (200 OK):
    {
        "user_id": "7ec7606c",
        "namespace": "openclaw-7ec7606c",
        "instance_name": "openclaw-7ec7606c",
        "status": {
            "phase": "Running",
            "conditions": [...]
        }
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
        # Verify user can only access their own instance
        user_email = session['user_email']
        authenticated_user_id = generate_user_id(user_email)
        if user_id != authenticated_user_id:
            logger.warning(f"⚠️ Unauthorized access attempt: {user_email} tried to access user_id {user_id}")
            return jsonify({
                "error": "Forbidden: You can only access your own instances"
            }), 403

        namespace = f"openclaw-{user_id}"
        instance_name = f"openclaw-{user_id}"
        instance_id = user_id  # instance_id is the unique identifier for the instance

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

        # Get runtime class and storage class from spec
        runtime_class = instance.get('spec', {}).get('availability', {}).get('runtimeClassName') or None
        storage_class = instance.get('spec', {}).get('storage', {}).get('persistence', {}).get('storageClass', 'efs-sc')

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
            api_gateway_url = f"{Config.API_GATEWAY_ENDPOINT}/{Config.API_GATEWAY_STAGE}/instance/{instance_id}/?token={gateway_token}"

        if Config.USE_PUBLIC_ALB and ready_for_connect and gateway_token:
            # CloudFront WebSocket URL (primary for new frontend)
            cloudfront_url = f"wss://{Config.CLOUDFRONT_DOMAIN}/instance/{instance_id}?token={gateway_token}"
            # CloudFront HTTP URL (for display in UI)
            cloudfront_http_url = f"https://{Config.CLOUDFRONT_DOMAIN}/instance/{instance_id}/?token={gateway_token}"

        response = {
            "user_id": user_id,
            "instance_id": instance_id,
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
            "runtime_class": runtime_class,  # 'kata-fc', 'kata-qemu', or None (runc)
            "storage_class": storage_class,  # 'gp3' or 'efs-sc'
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
    List all OpenClaw instances for the authenticated user

    Authentication: Requires valid session (user must be logged in)
    Authorization: Returns only instances owned by the authenticated user

    Response (200 OK):
    {
        "instances": [
            {
                "instance_id": "7ec7606c",
                "display_name": "My Instance",
                "namespace": "openclaw-7ec7606c",
                "status": "Running",
                "provider": "bedrock",
                "model": "claude-sonnet-4-5",
                "created_at": "2024-01-15T10:30:00Z",
                "cloudfront_http_url": "https://...",
                "ready_for_connect": true
            }
        ]
    }
    """
    try:
        # Get authenticated user
        user_email = session['user_email']
        user_id = generate_user_id(user_email)

        k8s_client = K8sClient()
        instances = []

        # List all namespaces starting with 'openclaw-'
        try:
            namespaces = k8s_client.core_v1.list_namespace(
                label_selector=f"openclaw.rocks/user-id={user_id}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not list namespaces: {str(e)}")
            namespaces = None

        # If no namespaces found, try the default user namespace
        if not namespaces or not namespaces.items:
            namespaces_to_check = [f"openclaw-{user_id}"]
        else:
            namespaces_to_check = [ns.metadata.name for ns in namespaces.items]

        # Fetch instances from each namespace
        from app.config import Config
        for namespace in namespaces_to_check:
            try:
                # List OpenClawInstance CRDs in this namespace
                crd_instances = k8s_client.custom_objects.list_namespaced_custom_object(
                    group="openclaw.rocks",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="openclawinstances"
                )

                for instance in crd_instances.get('items', []):
                    instance_name = instance.get('metadata', {}).get('name', '')
                    instance_id = instance_name.replace('openclaw-', '')

                    # Extract basic info
                    instance_status = instance.get('status', {})
                    phase = instance_status.get('phase', 'Pending')
                    created_at = instance.get('metadata', {}).get('creationTimestamp', '')

                    # Get LLM provider and model from labels
                    labels = instance.get('metadata', {}).get('labels', {})
                    llm_provider = labels.get('openclaw.rocks/llm-provider', 'bedrock')

                    # Get model from spec.config.raw.agents.defaults.model.primary
                    spec = instance.get('spec', {})
                    model_full = spec.get('config', {}).get('raw', {}).get('agents', {}).get('defaults', {}).get('model', {}).get('primary', '')
                    # Extract just the model ID part (after the last '/')
                    model_name = model_full.split('/')[-1] if model_full else 'Unknown'

                    # Check readiness
                    pods_ready = False
                    service_ready = False
                    gateway_token_exists = False

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
                    except:
                        pass

                    try:
                        endpoints = k8s_client.core_v1.read_namespaced_endpoints(
                            name=instance_name,
                            namespace=namespace
                        )
                        service_ready = bool(
                            endpoints.subsets and
                            any(subset.addresses for subset in endpoints.subsets)
                        )
                    except:
                        pass

                    try:
                        secret = k8s_client.core_v1.read_namespaced_secret(
                            name=f"{instance_name}-gateway-token",
                            namespace=namespace
                        )
                        gateway_token_exists = bool(secret.data.get('token'))
                    except:
                        pass

                    ready_for_connect = (
                        phase == 'Running' and
                        pods_ready and
                        service_ready and
                        gateway_token_exists
                    )

                    # Build CloudFront URL if ready
                    cloudfront_http_url = None
                    if Config.USE_PUBLIC_ALB and ready_for_connect:
                        try:
                            import base64
                            secret = k8s_client.core_v1.read_namespaced_secret(
                                name=f"{instance_name}-gateway-token",
                                namespace=namespace
                            )
                            gateway_token = base64.b64decode(secret.data.get('token', '')).decode('utf-8')
                            cloudfront_http_url = f"https://{Config.CLOUDFRONT_DOMAIN}/instance/{instance_id}/?token={gateway_token}"
                        except:
                            pass

                    # Extract display name from labels or use instance_id
                    display_name = labels.get('openclaw.rocks/display-name', instance_id)

                    instances.append({
                        "instance_id": instance_id,
                        "display_name": display_name,
                        "namespace": namespace,
                        "status": phase,
                        "provider": llm_provider,
                        "model": model_name,
                        "created_at": created_at,
                        "cloudfront_http_url": cloudfront_http_url,
                        "ready_for_connect": ready_for_connect
                    })

            except ApiException as e:
                if e.status == 404:
                    # Namespace or CRD not found, skip
                    continue
                logger.warning(f"⚠️ Could not list instances in {namespace}: {str(e)}")
            except Exception as e:
                logger.warning(f"⚠️ Error processing namespace {namespace}: {str(e)}")

        return jsonify({"instances": instances}), 200

    except Exception as e:
        logger.error(f"❌ Error listing instances: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
