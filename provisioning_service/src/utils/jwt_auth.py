"""JWT Authentication utilities for Cognito tokens"""
import requests
import logging
from functools import wraps
from flask import request, jsonify
from jose import jwt, JWTError
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class CognitoJWTVerifier:
    """Verify and decode Cognito JWT tokens"""

    def __init__(self, region: str, user_pool_id: str, client_id: str):
        self.region = region
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self._jwks_cache = None

    def _get_jwks(self) -> Dict:
        """Fetch JWKS (public keys) from Cognito"""
        if self._jwks_cache is None:
            try:
                response = requests.get(self.jwks_url, timeout=5)
                response.raise_for_status()
                self._jwks_cache = response.json()
                logger.info(f"✅ Fetched JWKS from Cognito: {len(self._jwks_cache.get('keys', []))} keys")
            except Exception as e:
                logger.error(f"❌ Failed to fetch JWKS: {e}")
                raise ValueError(f"Failed to fetch Cognito public keys: {e}")
        return self._jwks_cache

    def _get_signing_key(self, token: str) -> Dict:
        """Get the signing key for a token from JWKS"""
        try:
            # Decode header without verification to get kid
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')

            if not kid:
                raise ValueError("Token header missing 'kid'")

            # Find matching key in JWKS
            jwks = self._get_jwks()
            for key in jwks.get('keys', []):
                if key.get('kid') == kid:
                    return key

            raise ValueError(f"Signing key with kid '{kid}' not found in JWKS")
        except Exception as e:
            logger.error(f"❌ Failed to get signing key: {e}")
            raise

    def verify_token(self, token: str) -> Dict:
        """
        Verify and decode a Cognito JWT token

        Args:
            token: JWT token string

        Returns:
            Dict containing token claims (sub, email, cognito:username, etc.)

        Raises:
            ValueError: If token is invalid
        """
        try:
            # Get signing key
            signing_key = self._get_signing_key(token)

            # Verify and decode token
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=['RS256'],
                audience=self.client_id,
                issuer=self.issuer,
                options={
                    'verify_signature': True,
                    'verify_exp': True,
                    'verify_aud': True,
                    'verify_iss': True
                }
            )

            logger.debug(f"✅ Token verified for user: {claims.get('email', claims.get('sub'))}")
            return claims

        except JWTError as e:
            logger.error(f"❌ JWT verification failed: {e}")
            raise ValueError(f"Invalid token: {e}")
        except Exception as e:
            logger.error(f"❌ Token verification error: {e}")
            raise ValueError(f"Token verification failed: {e}")

    def extract_user_info(self, claims: Dict) -> Dict:
        """
        Extract user information from verified token claims

        Args:
            claims: Decoded JWT claims

        Returns:
            Dict with user_email, cognito_sub, username
        """
        return {
            'user_email': claims.get('email'),
            'cognito_sub': claims.get('sub'),
            'username': claims.get('cognito:username', claims.get('email')),
            'groups': claims.get('cognito:groups', [])
        }


def require_auth(verifier_getter):
    """
    Decorator to require valid JWT authentication

    Args:
        verifier_getter: Callable that returns CognitoJWTVerifier instance

    Usage:
        @provision_bp.route('/provision', methods=['POST'])
        @require_auth(lambda: current_app.jwt_verifier)
        def provision(user_info):
            # user_info contains verified user data
            user_email = user_info['user_email']
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get verifier from callable (allows access to current_app at request time)
            verifier = verifier_getter()

            # Extract token from Authorization header
            auth_header = request.headers.get('Authorization', '')

            if not auth_header.startswith('Bearer '):
                logger.warning("❌ Missing or invalid Authorization header")
                return jsonify({
                    'error': 'Unauthorized',
                    'message': 'Missing or invalid Authorization header'
                }), 401

            token = auth_header[7:]  # Remove 'Bearer ' prefix

            try:
                # Verify token and extract claims
                claims = verifier.verify_token(token)
                user_info = verifier.extract_user_info(claims)

                # Check if email exists
                if not user_info.get('user_email'):
                    logger.error("❌ Token missing email claim")
                    return jsonify({
                        'error': 'Unauthorized',
                        'message': 'Token missing email claim'
                    }), 401

                # Pass user_info to the route handler
                return f(user_info=user_info, *args, **kwargs)

            except ValueError as e:
                logger.warning(f"❌ Token verification failed: {e}")
                return jsonify({
                    'error': 'Unauthorized',
                    'message': str(e)
                }), 401
            except Exception as e:
                logger.error(f"❌ Authentication error: {e}")
                return jsonify({
                    'error': 'Internal Server Error',
                    'message': 'Authentication failed'
                }), 500

        return decorated_function
    return decorator


def get_user_from_request_optional(verifier: CognitoJWTVerifier) -> Optional[Dict]:
    """
    Extract user info from request if JWT token is present (optional auth)

    Returns:
        Dict with user info if valid token present, None otherwise
    """
    auth_header = request.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return None

    token = auth_header[7:]

    try:
        claims = verifier.verify_token(token)
        return verifier.extract_user_info(claims)
    except Exception as e:
        logger.debug(f"Optional auth failed: {e}")
        return None
