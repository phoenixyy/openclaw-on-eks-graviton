"""API endpoints"""
from flask import Blueprint

# Import blueprints
from app.api.register import register_bp
from app.api.login import login_bp
from app.api.provision import provision_bp
from app.api.status import status_bp
from app.api.delete import delete_bp
from app.api.health import health_bp
from app.api.proxy import proxy_bp
from app.api.devices import devices_bp
from app.api.models import models_bp

__all__ = ['register_bp', 'login_bp', 'provision_bp', 'status_bp', 'delete_bp', 'health_bp', 'proxy_bp', 'devices_bp', 'models_bp']
