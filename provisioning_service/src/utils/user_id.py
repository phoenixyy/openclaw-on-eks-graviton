"""User ID generation utilities"""
import hashlib

def generate_user_id(email: str) -> str:
    """
    Generate a user ID from email address

    Args:
        email: User email address

    Returns:
        8-character user ID (SHA-256 hash prefix)
    """
    # Normalize email to lowercase for consistency
    normalized_email = email.lower()
    return hashlib.sha256(normalized_email.encode()).hexdigest()[:8]
