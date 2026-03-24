"""Logging middleware"""
import logging
from pythonjsonlogger import jsonlogger
from app.config import Config
from flask import request, g
import time

def setup_logging(app):
    """Setup structured JSON logging"""

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, Config.LOG_LEVEL))

    # JSON formatter
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s'
    )

    # Console handler
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Request logging
    @app.before_request
    def before_request():
        g.start_time = time.time()

    @app.after_request
    def after_request(response):
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            logger.info(
                'request_completed',
                extra={
                    'method': request.method,
                    'path': request.path,
                    'status': response.status_code,
                    'duration_ms': int(duration * 1000),
                    'ip': request.remote_addr
                }
            )
        return response
