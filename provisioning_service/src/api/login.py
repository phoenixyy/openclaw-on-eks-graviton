"""Login API endpoint"""
from flask import Blueprint, request, jsonify, session
from app.database import get_user_by_email
from app.utils.session_auth import verify_password
import logging

login_bp = Blueprint('login', __name__)
logger = logging.getLogger(__name__)

@login_bp.route('/login', methods=['POST'])
def login():
    """
    Login with email and password

    Request Body:
    {
        "email": "john@example.com",
        "password": "securepass123"
    }

    Response (200 OK):
    {
        "status": "success",
        "message": "Login successful",
        "user": {
            "username": "johndoe",
            "email": "john@example.com"
        }
    }

    Response (401 Unauthorized):
    {
        "error": "Invalid email or password"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body is required"}), 400

        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        # Validate inputs
        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        # Get user from database
        user = get_user_by_email(email)

        if not user:
            logger.warning(f"⚠️ Login failed: User not found ({email})")
            return jsonify({"error": "Invalid email or password"}), 401

        # Verify password
        if not verify_password(password, user['password_hash']):
            logger.warning(f"⚠️ Login failed: Invalid password ({email})")
            return jsonify({"error": "Invalid email or password"}), 401

        # Create session
        session['user_email'] = user['email']
        session['username'] = user['username']
        session.permanent = True  # Use permanent session (will last for app.permanent_session_lifetime)

        logger.info(f"✅ User logged in: {user['username']} ({user['email']})")

        return jsonify({
            "status": "success",
            "message": "Login successful",
            "user": {
                "username": user['username'],
                "email": user['email']
            }
        }), 200

    except Exception as e:
        logger.error(f"❌ Error during login: {str(e)}", exc_info=True)
        return jsonify({"error": "Login failed. Please try again."}), 500

@login_bp.route('/logout', methods=['POST'])
def logout():
    """
    Logout current user

    Response (200 OK):
    {
        "status": "success",
        "message": "Logout successful"
    }
    """
    try:
        user_email = session.get('user_email', 'unknown')

        # Clear session
        session.clear()

        logger.info(f"✅ User logged out: {user_email}")

        return jsonify({
            "status": "success",
            "message": "Logout successful"
        }), 200

    except Exception as e:
        logger.error(f"❌ Error during logout: {str(e)}", exc_info=True)
        return jsonify({"error": "Logout failed"}), 500

@login_bp.route('/me', methods=['GET'])
def me():
    """
    Get current logged-in user info

    Response (200 OK):
    {
        "user": {
            "username": "johndoe",
            "email": "john@example.com"
        }
    }

    Response (401 Unauthorized):
    {
        "error": "Not logged in"
    }
    """
    if 'user_email' not in session:
        return jsonify({"error": "Not logged in"}), 401

    return jsonify({
        "user": {
            "username": session.get('username'),
            "email": session.get('user_email')
        }
    }), 200
