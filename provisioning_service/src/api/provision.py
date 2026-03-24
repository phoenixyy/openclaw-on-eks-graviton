"""Provision API endpoint"""
from flask import Blueprint, request, jsonify, session
from app.k8s.client import K8sClient
from app.k8s.namespace import create_namespace
# from app.k8s.quota import create_resource_quota  # Disabled: operator's init-config lacks resources
from app.k8s.netpol import create_network_policy
from app.k8s.instance import create_openclaw_instance
from app.aws.iam import create_pod_identity_role, create_pod_identity_association
from app.utils.user_id import generate_user_id
from app.utils.validator import validate_email
from app.utils.session_auth import require_auth
from app.config import Config
import logging

provision_bp = Blueprint('provision', __name__)
logger = logging.getLogger(__name__)

@provision_bp.route('/provision', methods=['POST'])
@require_auth
def provision():
    """
    Create an OpenClaw instance

    Authentication: Requires valid session (user must be logged in)

    Request Body (optional):
    {
        "config": {  # optional, overrides defaults
            "resources": {
                "requests": {"cpu": "1", "memory": "2Gi"}
            }
        }
    }

    Response (201 Created):
    {
        "status": "created",
        "user_id": "7ec7606c",
        "namespace": "openclaw-7ec7606c",
        "instance_name": "openclaw-7ec7606c",
        "gateway_endpoint": "openclaw-7ec7606c.openclaw-7ec7606c.svc:18789",
        "message": "Instance created successfully"
    }

    Response (200 OK) - if already exists:
    {
        "status": "exists",
        ...
    }
    """
    try:
        # Get user info from session
        user_email = session['user_email']
        username = session.get('username', user_email)

        # Validate email (should always be valid from session, but double-check)
        if not user_email or not validate_email(user_email):
            return jsonify({
                "error": "Invalid email in session",
                "hint": "Please login again"
            }), 400

        # Get custom config from request body (optional)
        data = request.get_json() or {}
        custom_config = data.get('config', {})

        # Get provider choice (default: bedrock)
        provider = data.get('provider', 'bedrock')
        if provider not in ('bedrock', 'siliconflow'):
            return jsonify({"error": "Invalid provider. Must be 'bedrock' or 'siliconflow'"}), 400

        # Validate model selection
        selected_model = None
        if provider == 'bedrock':
            selected_model = data.get('model')
            if selected_model:
                valid_model_ids = [m['id'] for m in Config.BEDROCK_MODELS]
                if selected_model not in valid_model_ids:
                    return jsonify({"error": f"Invalid model. Must be one of: {valid_model_ids}"}), 400
        elif provider == 'siliconflow':
            selected_model = data.get('model')
            if selected_model:
                valid_model_ids = [m['id'] for m in Config.SILICONFLOW_MODELS]
                if selected_model not in valid_model_ids:
                    return jsonify({"error": f"Invalid model. Must be one of: {valid_model_ids}"}), 400

        # Validate SiliconFlow API key (user-provided)
        siliconflow_api_key = None
        if provider == 'siliconflow':
            siliconflow_api_key = data.get('siliconflow_api_key', '').strip()
            if not siliconflow_api_key:
                return jsonify({"error": "SiliconFlow API key is required"}), 400

        # Generate user_id
        user_id = generate_user_id(user_email)
        namespace = f"openclaw-{user_id}"
        instance_name = f"openclaw-{user_id}"

        logger.info(f"📥 Provisioning request: {user_email} (user_id: {user_id}, provider: {provider})")

        # Initialize K8s client
        k8s_client = K8sClient()

        # Create Namespace
        ns, ns_created = create_namespace(k8s_client, user_id)

        # Create ResourceQuota - DISABLED: operator's init-config container lacks resources spec
        # quota, quota_created = create_resource_quota(k8s_client, namespace)

        # Create NetworkPolicy
        netpol, netpol_created = create_network_policy(k8s_client, namespace)

        # Create Pod Identity Association (only, no role creation)
        role_arn = None
        pod_identity_association_id = None
        if Config.USE_POD_IDENTITY and provider == 'bedrock':
            # Use shared Bedrock Role (pre-created)
            role_arn = Config.SHARED_BEDROCK_ROLE_ARN
            logger.info(f"🔐 Using shared Bedrock IAM Role: {role_arn}")

            # Create Pod Identity Association (link SA to shared Role)
            service_account = f"openclaw-{user_id}"
            logger.info(f"🔗 Creating Pod Identity Association: {namespace}/{service_account} → {role_arn}")

            pod_identity_association_id = create_pod_identity_association(
                cluster_name=Config.EKS_CLUSTER_NAME,
                namespace=namespace,
                service_account=service_account,
                role_arn=role_arn,
                region=Config.AWS_REGION
            )

            if pod_identity_association_id:
                logger.info(f"✅ Pod Identity Association created: {pod_identity_association_id}")
                # Wait for Association to be fully active and webhook to sync (5 seconds)
                # This prevents race condition where Pod is created before webhook recognizes the Association
                import time
                logger.info(f"⏳ Waiting 5 seconds for Pod Identity Association to sync with webhook...")
                time.sleep(5)
                logger.info(f"✅ Pod Identity Association sync delay completed")
            else:
                logger.error(f"❌ Failed to create Pod Identity Association")

        # Create OpenClawInstance
        instance, instance_created = create_openclaw_instance(
            k8s_client,
            user_id,
            namespace,
            user_email,
            cognito_sub=None,  # No longer using Cognito
            custom_config=custom_config,
            role_arn=role_arn,
            provider=provider,
            siliconflow_api_key=siliconflow_api_key,
            model=selected_model
        )

        # Build response
        status = "created" if instance_created else "exists"
        gateway_endpoint = f"{instance_name}.{namespace}.svc:18789"

        response = {
            "status": status,
            "user_id": user_id,
            "namespace": namespace,
            "instance_name": instance_name,
            "gateway_endpoint": gateway_endpoint,
            "message": f"Instance {status} successfully",
            "resources_created": {
                "namespace": ns_created,
                # "resource_quota": quota_created,  # Removed: not creating ResourceQuota
                "network_policy": netpol_created,
                "openclaw_instance": instance_created,
                "iam_role": role_arn is not None if Config.USE_POD_IDENTITY else None,
                "pod_identity_association": pod_identity_association_id is not None if Config.USE_POD_IDENTITY else None
            }
        }

        if Config.USE_POD_IDENTITY and role_arn:
            response["iam_role_arn"] = role_arn
            if pod_identity_association_id:
                response["pod_identity_association_id"] = pod_identity_association_id

        status_code = 201 if instance_created else 200
        logger.info(f"✅ Provisioning completed: {user_email} ({status})")

        return jsonify(response), status_code

    except Exception as e:
        logger.error(f"❌ Error provisioning instance: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
