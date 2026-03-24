"""Register API endpoint"""
from flask import Blueprint, request, jsonify
from app.database import get_user_by_email, get_user_by_username, create_user
from app.utils.session_auth import hash_password
from app.utils.validator import validate_email
import logging
import re

register_bp = Blueprint('register', __name__)
logger = logging.getLogger(__name__)

def validate_username(username: str) -> tuple[bool, str]:
    """
    Validate username format

    Rules:
    - 3-30 characters
    - Alphanumeric, underscore, hyphen only
    - Must start with letter or number

    Returns:
        (is_valid, error_message)
    """
    if not username or len(username) < 3 or len(username) > 30:
        return False, "Username must be 3-30 characters long"

    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', username):
        return False, "Username can only contain letters, numbers, underscore, and hyphen"

    return True, ""

def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password strength

    Rules:
    - At least 8 characters
    - Contains at least one letter and one number

    Returns:
        (is_valid, error_message)
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long"

    if not re.search(r'[a-zA-Z]', password):
        return False, "Password must contain at least one letter"

    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number"

    return True, ""

@register_bp.route('/register', methods=['POST'])
def register():
    """
    Register a new user

    Request Body:
    {
        "username": "johndoe",
        "email": "john@example.com",
        "password": "securepass123"
    }

    Response (201 Created):
    {
        "status": "success",
        "message": "User registered successfully",
        "username": "johndoe",
        "email": "john@example.com"
    }

    Response (400 Bad Request):
    {
        "error": "Validation error message"
    }

    Response (409 Conflict):
    {
        "error": "User already exists"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body is required"}), 400

        username = data.get('username', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        # Validate inputs
        if not username or not email or not password:
            return jsonify({"error": "Username, email, and password are required"}), 400

        # Validate username
        is_valid, error_msg = validate_username(username)
        if not is_valid:
            return jsonify({"error": error_msg}), 400

        # Validate email
        if not validate_email(email):
            return jsonify({"error": "Invalid email format"}), 400

        # Validate password
        is_valid, error_msg = validate_password(password)
        if not is_valid:
            return jsonify({"error": error_msg}), 400

        # Check if user already exists
        if get_user_by_email(email):
            logger.warning(f"⚠️ Registration failed: Email already exists ({email})")
            return jsonify({"error": "Email already registered"}), 409

        if get_user_by_username(username):
            logger.warning(f"⚠️ Registration failed: Username already exists ({username})")
            return jsonify({"error": "Username already taken"}), 409

        # Hash password
        password_hash = hash_password(password)

        # Create user
        user_id = create_user(username, email, password_hash)

        logger.info(f"✅ User registered: {username} ({email})")

        return jsonify({
            "status": "success",
            "message": "User registered successfully",
            "username": username,
            "email": email
        }), 201

    except Exception as e:
        logger.error(f"❌ Error during registration: {str(e)}", exc_info=True)
        return jsonify({"error": "Registration failed. Please try again."}), 500
