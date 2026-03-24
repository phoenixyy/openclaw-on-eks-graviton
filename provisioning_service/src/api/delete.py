"""Delete API endpoint"""
from flask import Blueprint, jsonify, session
from app.k8s.client import K8sClient
from app.aws.iam import (
    delete_pod_identity_role,
    delete_pod_identity_association,
    list_pod_identity_associations
)
from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
from app.config import Config
from kubernetes.client.rest import ApiException
import logging

delete_bp = Blueprint('delete', __name__)
logger = logging.getLogger(__name__)

@delete_bp.route('/delete/<user_id>', methods=['DELETE'])
@require_auth
def delete(user_id):
    """
    Delete an OpenClaw instance and its namespace

    Authentication: Requires valid session (user must be logged in)
    Authorization: Users can only delete their own instances

    Args:
        user_id: User ID

    Response (200 OK):
    {
        "status": "deleted",
        "user_id": "7ec7606c",
        "message": "Instance deleted successfully"
    }

    Response (403 Forbidden):
    {
        "error": "Forbidden: You can only delete your own instances"
    }

    Response (404 Not Found):
    {
        "error": "Instance not found"
    }
    """
    try:
        # Verify user can only delete their own instance
        user_email = session['user_email']
        authenticated_user_id = generate_user_id(user_email)
        if user_id != authenticated_user_id:
            logger.warning(f"⚠️ Unauthorized delete attempt: {user_email} tried to delete user_id {user_id}")
            return jsonify({
                "error": "Forbidden: You can only delete your own instances"
            }), 403

        namespace = f"openclaw-{user_id}"

        logger.info(f"🗑️  Delete request for user: {user_id} ({user_email})")

        k8s_client = K8sClient()

        # Delete Pod Identity Association and IAM Role (if enabled)
        pod_identity_deleted = False
        iam_role_deleted = False

        if Config.USE_POD_IDENTITY:
            service_account = f"openclaw-{user_id}"

            # Delete Pod Identity Associations
            logger.info(f"🔗 Deleting Pod Identity Associations for {namespace}/{service_account}")
            association_ids = list_pod_identity_associations(
                cluster_name=Config.EKS_CLUSTER_NAME,
                namespace=namespace,
                service_account=service_account,
                region=Config.AWS_REGION
            )

            for association_id in association_ids:
                success = delete_pod_identity_association(
                    cluster_name=Config.EKS_CLUSTER_NAME,
                    association_id=association_id,
                    region=Config.AWS_REGION
                )
                if success:
                    logger.info(f"✅ Deleted Pod Identity Association: {association_id}")
                    pod_identity_deleted = True

            # Skip IAM Role deletion (using shared role)
            iam_role_deleted = False
            if Config.CREATE_IAM_ROLE_PER_USER:
                # Only delete if using per-user roles (legacy mode)
                logger.info(f"🔐 Deleting IAM Role for user_id: {user_id}")
                iam_role_deleted = delete_pod_identity_role(user_id, region=Config.AWS_REGION)
                if iam_role_deleted:
                    logger.info(f"✅ Deleted IAM Role: openclaw-user-{user_id}")
            else:
                logger.info(f"ℹ️  Skipping IAM Role deletion (using shared role)")

        # Delete namespace (will cascade delete all resources)
        k8s_client.core_v1.delete_namespace(
            name=namespace,
            body={}
        )

        logger.info(f"✅ Deleted namespace: {namespace}")

        response = {
            "status": "deleted",
            "user_id": user_id,
            "namespace": namespace,
            "message": "Instance deleted successfully"
        }

        if Config.USE_POD_IDENTITY:
            response["resources_deleted"] = {
                "namespace": True,
                "pod_identity_association": pod_identity_deleted,
                "iam_role": iam_role_deleted
            }

        return jsonify(response), 200

    except ApiException as e:
        if e.status == 404:
            return jsonify({"error": "Instance not found"}), 404
        logger.error(f"❌ Error deleting instance: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"❌ Error deleting instance: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
