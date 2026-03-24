"""Main Flask application"""
from flask import Flask, render_template, send_from_directory
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix
from kubernetes import config
import logging
import os
import threading

logger = logging.getLogger(__name__)

class StripProdPrefixMiddleware:
    """WSGI Middleware to strip /prod prefix before Flask routing"""
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path.startswith('/prod/'):
            environ['PATH_INFO'] = path[5:]  # Remove '/prod'
            logger.debug(f"Stripped /prod prefix: {path} -> {environ['PATH_INFO']}")
        return self.app(environ, start_response)

def create_app():
    """Factory function to create Flask application"""
    # Set template and static folders
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), 'static')

    app = Flask(__name__,
                template_folder=template_dir,
                static_folder=static_dir)

    # Load configuration
    app.config.from_object('app.config.Config')

    # Configure SQLAlchemy URI for Flask-Session
    pg_host = os.environ.get('POSTGRES_HOST', 'postgres')
    pg_port = os.environ.get('POSTGRES_PORT', '5432')
    pg_db = os.environ.get('POSTGRES_DB', 'openclaw')
    pg_user = os.environ.get('POSTGRES_USER', 'openclaw')
    pg_password = os.environ.get('POSTGRES_PASSWORD', 'OpenClaw2026!SecureDB')

    app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize SQLAlchemy for Flask-Session
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy(app)
    app.config['SESSION_SQLALCHEMY'] = db

    # Initialize database tables (users, sessions, usage_events, etc.)
    from app.database import init_db
    init_db()
    logger.info("✅ Database initialized (including sessions table)")

    # Initialize Flask-Session (uses SQLAlchemy)
    Session(app)
    logger.info("✅ Session management initialized (using PostgreSQL)")

    # Initialize Kubernetes client (in-cluster)
    try:
        config.load_incluster_config()
        logger.info("✅ Kubernetes in-cluster config loaded")
    except config.ConfigException:
        # Fallback to kubeconfig (for local development)
        try:
            config.load_kube_config()
            logger.info("✅ Kubernetes kubeconfig loaded (dev mode)")
        except Exception as e:
            logger.error(f"❌ Failed to load Kubernetes config: {e}")
            raise

    # Register blueprints (API endpoints)
    from app.api import register_bp, login_bp, provision_bp, status_bp, delete_bp, health_bp, proxy_bp, devices_bp, models_bp
    from app.api.billing import billing_bp
    from app.api.admin import admin_bp

    app.register_blueprint(register_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(provision_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(delete_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(proxy_bp)  # Reverse proxy for instance access
    app.register_blueprint(devices_bp)  # Device pairing API
    app.register_blueprint(models_bp)  # Model listing API
    app.register_blueprint(billing_bp)  # Billing API
    app.register_blueprint(admin_bp)  # Admin API

    # Initialize flask-sock AFTER blueprint registration (registers WebSocket routes on the app)
    try:
        from app.api.proxy import sock as proxy_sock
        if proxy_sock is not None:
            proxy_sock.init_app(app)
            logger.info("✅ flask-sock initialized (WebSocket support enabled)")
    except Exception as e:
        logger.warning(f"⚠️ flask-sock init skipped: {e}")

    # Frontend routes
    @app.route('/')
    def index():
        """Redirect to login page"""
        from flask import redirect
        return redirect('/login')

    @app.route('/login')
    def login_page():
        """Serve login page"""
        return render_template('login-simple.html')

    @app.route('/dashboard')
    def dashboard():
        """Serve dashboard page"""
        return render_template('dashboard-new.html')

    @app.route('/test')
    def test_dashboard():
        """Serve test dashboard (no auth required)"""
        return render_template('dashboard-test.html')

    @app.route('/admin')
    def admin_dashboard():
        """Serve admin dashboard page"""
        return render_template('admin-dashboard.html')

    # Serve static files explicitly (for cases where static_folder doesn't work)
    @app.route('/static/<path:filename>')
    def serve_static(filename):
        """Serve static files"""
        return send_from_directory(app.static_folder, filename)

    # Setup middlewares
    from app.middleware import setup_middlewares
    setup_middlewares(app)

    # Apply ProxyFix middleware (for CloudFront → ALB → Pod setup)
    # This allows Flask to correctly identify HTTPS requests from X-Forwarded-Proto header
    # Critical for SESSION_COOKIE_SECURE to work properly
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,      # Trust X-Forwarded-For (1 proxy)
        x_proto=1,    # Trust X-Forwarded-Proto (1 proxy) - CRITICAL for HTTPS detection
        x_host=1,     # Trust X-Forwarded-Host (1 proxy)
        x_prefix=1    # Trust X-Forwarded-Prefix (1 proxy)
    )

    # Apply WSGI middleware to strip /prod prefix (must be last)
    app.wsgi_app = StripProdPrefixMiddleware(app.wsgi_app)

    # Start background usage collector (only in production)
    if not app.config['DEBUG']:
        try:
            from app.services.usage_collector import UsageCollector
            collector = UsageCollector(interval=300)  # 5 minutes
            collector_thread = threading.Thread(target=collector.run, daemon=True, name='UsageCollector')
            collector_thread.start()
            logger.info("✅ Usage collector started (5-minute interval)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to start usage collector: {e}")
    else:
        logger.info("ℹ️ Usage collector disabled (DEBUG mode)")

    logger.info("🚀 OpenClaw Provisioning Service initialized")
    logger.info(f"📁 Template folder: {template_dir}")
    logger.info(f"📁 Static folder: {static_dir}")
    return app

# Create app instance
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=app.config['DEBUG'])
