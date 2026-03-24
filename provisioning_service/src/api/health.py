"""Health check endpoint"""
from flask import Blueprint, jsonify
from app.k8s.client import K8sClient
import logging

health_bp = Blueprint('health', __name__)
logger = logging.getLogger(__name__)

@health_bp.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint

    Response (200 OK):
    {
        "status": "healthy",
        "k8s_api": "connected"
    }

    Response (503 Service Unavailable):
    {
        "status": "unhealthy",
        "k8s_api": "disconnected",
        "error": "..."
    }
    """
    try:
        # Test K8s API connectivity
        k8s_client = K8sClient()
        k8s_client.core_v1.list_namespace(limit=1)

        return jsonify({
            "status": "healthy",
            "k8s_api": "connected"
        }), 200

    except Exception as e:
        logger.error(f"❌ Health check failed: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "k8s_api": "disconnected",
            "error": str(e)
        }), 503
