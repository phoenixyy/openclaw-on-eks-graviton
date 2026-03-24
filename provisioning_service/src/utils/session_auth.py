"""Session-based authentication utilities"""
import bcrypt
import logging
from functools import wraps
from flask import session, request, jsonify
from typing import Optional

logger = logging.getLogger(__name__)

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as e:
        logger.error(f"❌ Password verification error: {e}")
        return False

def require_auth(f):
    """
    Decorator to require valid session authentication

    Usage:
        @provision_bp.route('/provision', methods=['POST'])
        @require_auth
        def provision():
            user_email = session['user_email']
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in (session contains user_email)
        if 'user_email' not in session:
            logger.warning("❌ Unauthorized access attempt - no session")
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Please login to access this resource'
            }), 401

        # User is authenticated, proceed
        logger.debug(f"✅ Authenticated user: {session['user_email']}")
        return f(*args, **kwargs)

    return decorated_function

def get_current_user() -> Optional[dict]:
    """
    Get current logged-in user from session

    Returns:
        Dict with user info if logged in, None otherwise
    """
    if 'user_email' not in session:
        return None

    return {
        'user_email': session.get('user_email'),
        'username': session.get('username')
    }

def require_admin(f):
    """
    Decorator to require admin role

    Usage:
        @admin_bp.route('/admin/users', methods=['GET'])
        @require_auth
        @require_admin
        def list_all_users():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in
        if 'user_email' not in session:
            logger.warning("❌ Unauthorized access attempt - no session")
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Please login to access this resource'
            }), 401

        # Check if user is admin
        from app.database import get_user_by_email
        user = get_user_by_email(session['user_email'])

        if not user or not user.get('is_admin'):
            logger.warning(f"❌ Forbidden access attempt by non-admin: {session['user_email']}")
            return jsonify({
                'error': 'Forbidden',
                'message': 'Admin access required'
            }), 403

        # User is admin, proceed
        logger.debug(f"✅ Admin access granted: {session['user_email']}")
        return f(*args, **kwargs)

    return decorated_function
