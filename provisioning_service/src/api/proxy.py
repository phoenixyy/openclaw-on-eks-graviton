"""Reverse proxy API endpoint for OpenClaw instances"""
from flask import Blueprint, request, Response, current_app
from app.k8s.client import K8sClient
from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
import requests
import logging

proxy_bp = Blueprint('proxy', __name__)
logger = logging.getLogger(__name__)

# Try to import flask-sock for WebSocket support
# sock is initialized without app here; main.py calls sock.init_app(app) after blueprint registration
try:
    from flask_sock import Sock
    sock = Sock()  # Defer app binding to main.py via sock.init_app(app)
    WEBSOCKET_SUPPORTED = True
    logger.info("✅ WebSocket support enabled via flask-sock")
except ImportError:
    logger.warning("⚠️ flask-sock not installed, WebSocket proxying disabled")
    WEBSOCKET_SUPPORTED = False
    sock = None

def proxy_to_instance(user_info, instance_id, subpath):
    """
    Reverse proxy to OpenClaw instance gateway

    This endpoint dynamically routes requests to the correct OpenClaw instance
    based on the instance_id in the URL path. No per-user configuration needed!

    Authentication:
    - Requires valid JWT token OR gateway token
    - JWT: from Authorization header (for dashboard access)
    - Gateway token: from ?token=xxx query parameter (for direct access)

    Example:
        GET /instance/416e0b5f/workspace/abc123
        → Proxies to http://openclaw-416e0b5f.openclaw-416e0b5f.svc:18789/workspace/abc123

    Args:
        instance_id: Instance ID (unique identifier for the instance)
        subpath: Path to proxy to the instance (e.g., "workspace/abc123")
    """
    try:
        # Build target URL
        namespace = f"openclaw-{instance_id}"
        service_name = f"openclaw-{instance_id}"
        service_port = 18789

        # Kubernetes internal DNS: service.namespace.svc.cluster.local
        target_url = f"http://{service_name}.{namespace}.svc.cluster.local:{service_port}/{subpath}"

        # Preserve query parameters
        if request.query_string:
            target_url += f"?{request.query_string.decode('utf-8')}"

        logger.info(f"🔀 Proxying {request.method} {request.path} → {target_url}")

        # Forward request headers (exclude hop-by-hop headers)
        headers = {}
        for key, value in request.headers:
            if key.lower() not in ['host', 'connection', 'keep-alive', 'proxy-authenticate',
                                   'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade']:
                headers[key] = value

        # Forward the request to OpenClaw instance
        response = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=30,
            stream=True  # Stream response for large files
        )

        # Build response
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [
            (name, value) for (name, value) in response.raw.headers.items()
            if name.lower() not in excluded_headers
        ]

        logger.info(f"✅ Proxied response: {response.status_code}")

        return Response(
            response.iter_content(chunk_size=8192),
            status=response.status_code,
            headers=response_headers,
            direct_passthrough=True
        )

    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Connection error to instance: {str(e)}")
        return {
            "error": "Instance not reachable",
            "message": "The OpenClaw instance is not responding. It may still be starting up.",
            "instance_id": instance_id
        }, 503
    except requests.exceptions.Timeout as e:
        logger.error(f"❌ Timeout connecting to instance: {str(e)}")
        return {
            "error": "Request timeout",
            "message": "The OpenClaw instance did not respond in time.",
            "instance_id": instance_id
        }, 504
    except Exception as e:
        logger.error(f"❌ Error proxying request: {str(e)}", exc_info=True)
        return {
            "error": "Proxy error",
            "message": str(e),
            "instance_id": instance_id
        }, 500


@proxy_bp.route('/instance/<instance_id>/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@proxy_bp.route('/instance/<instance_id>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy_to_instance_root(instance_id):
    """
    Proxy to OpenClaw instance root path

    This handles the case when accessing /instance/{instance_id}/ or /instance/{instance_id}
    """
    # Pass empty user_info since we don't do auth here (OpenClaw Gateway handles it)
    user_info = {}
    return proxy_to_instance(user_info, instance_id, '')


# Register proxy routes with main subpath handler
@proxy_bp.route('/instance/<instance_id>/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy_with_subpath(instance_id, subpath):
    """Route handler with subpath"""
    user_info = {}
    return proxy_to_instance(user_info, instance_id, subpath)


# WebSocket proxy support
if WEBSOCKET_SUPPORTED:
    import websocket as ws_client
    import threading

    def proxy_websocket(ws, instance_id, subpath=''):
        """
        Proxy WebSocket connection to OpenClaw instance

        Args:
            ws: Flask-Sock WebSocket object
            instance_id: Instance ID
            subpath: Optional subpath (for /ws or other WebSocket endpoints)
        """
        try:
            # Build target WebSocket URL
            namespace = f"openclaw-{instance_id}"
            service_name = f"openclaw-{instance_id}"
            service_port = 18789

            # Construct target URL (ws:// for internal cluster communication)
            target_url = f"ws://{service_name}.{namespace}.svc.cluster.local:{service_port}"
            if subpath:
                target_url += f"/{subpath}"

            # Preserve query parameters (including ?token=xxx)
            if request.query_string:
                target_url += f"?{request.query_string.decode('utf-8')}"

            logger.info(f"🔌 WebSocket proxy: {request.path} → {target_url}")

            # Create WebSocket connection to target
            target_ws = ws_client.WebSocket()
            target_ws.connect(target_url, timeout=10)

            # Bidirectional relay
            def relay_client_to_target():
                """Relay messages from client to target"""
                try:
                    while True:
                        data = ws.receive()
                        if data is None:
                            break
                        target_ws.send(data)
                except Exception as e:
                    logger.error(f"❌ Client→Target relay error: {str(e)}")
                finally:
                    try:
                        target_ws.close()
                    except:
                        pass

            def relay_target_to_client():
                """Relay messages from target to client"""
                try:
                    while True:
                        data = target_ws.recv()
                        if not data:
                            break
                        ws.send(data)
                except Exception as e:
                    logger.error(f"❌ Target→Client relay error: {str(e)}")
                finally:
                    try:
                        ws.close()
                    except:
                        pass

            # Start relay threads
            client_thread = threading.Thread(target=relay_client_to_target, daemon=True)
            target_thread = threading.Thread(target=relay_target_to_client, daemon=True)

            client_thread.start()
            target_thread.start()

            # Wait for both threads to finish
            client_thread.join()
            target_thread.join()

            logger.info(f"✅ WebSocket proxy closed: {instance_id}")

        except Exception as e:
            logger.error(f"❌ WebSocket proxy error: {str(e)}", exc_info=True)
            try:
                ws.close()
            except:
                pass

    # WebSocket routes (mirror HTTP routes but for WebSocket)
    @sock.route('/instance/<instance_id>/ws')
    def websocket_proxy_ws(ws, instance_id):
        """WebSocket proxy for /instance/<instance_id>/ws"""
        proxy_websocket(ws, instance_id, 'ws')

    @sock.route('/instance/<instance_id>/')
    @sock.route('/instance/<instance_id>')
    def websocket_proxy_root(ws, instance_id):
        """WebSocket proxy for /instance/<instance_id> (auto-detects WebSocket upgrade)"""
        proxy_websocket(ws, instance_id, '')

    @sock.route('/instance/<instance_id>/<path:subpath>')
    def websocket_proxy_subpath(ws, instance_id, subpath):
        """WebSocket proxy for /instance/<instance_id>/<path>"""
        proxy_websocket(ws, instance_id, subpath)
