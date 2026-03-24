"""CORS middleware configuration"""
from flask_cors import CORS
import logging

logger = logging.getLogger(__name__)

def setup_cors(app):
    """
    Setup CORS for CloudFront + ALB architecture

    Allows credentials (session cookies) from CloudFront domain
    """
    from app.config import Config

    allowed_origins = []

    # CloudFront domain (primary)
    if Config.CLOUDFRONT_DOMAIN:
        allowed_origins.append(f"https://{Config.CLOUDFRONT_DOMAIN}")

    # Public ALB domain (direct access)
    if Config.PUBLIC_ALB_DNS:
        allowed_origins.append(f"http://{Config.PUBLIC_ALB_DNS}")
        allowed_origins.append(f"https://{Config.PUBLIC_ALB_DNS}")

    # API Gateway (legacy)
    if Config.API_GATEWAY_ENDPOINT:
        allowed_origins.append(Config.API_GATEWAY_ENDPOINT)

    logger.info(f"🌐 Configuring CORS with allowed origins: {allowed_origins}")

    CORS(app,
         origins=allowed_origins,
         supports_credentials=True,  # Allow cookies
         allow_headers=['Content-Type', 'Authorization'],
         methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'],
         expose_headers=['Content-Type', 'X-Request-ID'])

    logger.info("✅ CORS configured successfully")
