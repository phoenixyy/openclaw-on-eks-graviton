"""Models API endpoint"""
from flask import Blueprint, jsonify
from app.config import Config

models_bp = Blueprint('models', __name__)


@models_bp.route('/models', methods=['GET'])
def list_models():
    """Return available models grouped by provider (public, no auth required)"""
    return jsonify({
        "bedrock": Config.BEDROCK_MODELS,
        "siliconflow": Config.SILICONFLOW_MODELS,
    })
