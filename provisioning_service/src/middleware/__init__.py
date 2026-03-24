"""Middleware configuration"""
from app.middleware.logging import setup_logging
from app.middleware.error_handler import setup_error_handlers
from app.middleware.cors import setup_cors

def setup_middlewares(app):
    """Setup all middlewares"""
    setup_cors(app)  # Must be first for CORS to work
    setup_logging(app)
    setup_error_handlers(app)
