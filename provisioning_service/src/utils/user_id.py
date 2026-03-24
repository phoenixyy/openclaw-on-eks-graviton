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

def generate_instance_id(user_id: str, sequence_number: int) -> str:
    """
    Generate an instance ID from user_id and sequence number

    Args:
        user_id: 8-character user ID
        sequence_number: Sequence number (0-99)

    Returns:
        instance ID in format: {user_id}-{2-digit sequence}
        Example: 7ec7606c-01, 7ec7606c-02
    """
    if sequence_number < 0 or sequence_number > 99:
        raise ValueError("Sequence number must be between 0 and 99")
    return f"{user_id}-{sequence_number:02d}"
