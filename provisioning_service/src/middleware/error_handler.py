"""Error handling middleware"""
from flask import jsonify
from kubernetes.client.rest import ApiException
import logging

logger = logging.getLogger(__name__)

def setup_error_handlers(app):
    """Setup global error handlers"""

    @app.errorhandler(ApiException)
    def handle_k8s_error(error):
        """Handle Kubernetes API errors"""
        logger.error(f"Kubernetes API error: {error}", exc_info=True)
        return jsonify({
            "error": "Kubernetes API error",
            "message": error.reason,
            "status": error.status
        }), 500

    @app.errorhandler(Exception)
    def handle_generic_error(error):
        """Handle all other errors"""
        logger.error(f"Unhandled exception: {error}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "message": str(error)
        }), 500

    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 errors"""
        return jsonify({
            "error": "Not found",
            "message": "The requested resource was not found"
        }), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        """Handle 405 errors"""
        return jsonify({
            "error": "Method not allowed",
            "message": "The method is not allowed for the requested URL"
        }), 405
