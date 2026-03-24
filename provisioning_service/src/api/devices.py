"""Device pairing API endpoints"""
from flask import Blueprint, request, jsonify, current_app, session
from app.utils.session_auth import require_auth
from app.utils.user_id import generate_user_id
from app.utils.pod_exec import exec_in_pod, check_pod_exists
import logging

devices_bp = Blueprint('devices', __name__)
logger = logging.getLogger(__name__)


@devices_bp.route('/api/devices/approve', methods=['POST'])
@require_auth
def approve_device():
    """
    Approve device pairing for OpenClaw instance

    Request Body:
    {
        "user_id": "7ec7606c",
        "request_id": "d5fd3ea8-..." (optional - auto-finds pending if not provided)
    }

    Response:
    {
        "success": true,
        "output": "Device approved successfully",
        "user_id": "7ec7606c",
        "request_id": "..."
    }

    Authorization:
    - Users can only approve devices for their own instances
    - JWT token required (extracted from Authorization header)
    """
    try:
        # 1. Validate input
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body is required"}), 400

        user_id = data.get('user_id')
        request_id = data.get('request_id')  # Now optional

        if not user_id:
            return jsonify({"error": "user_id is required"}), 400

        # 2. Authorization check - users can only approve devices for their own instances
        user_email = session['user_email']
        authenticated_user_id = generate_user_id(user_email)
        if user_id != authenticated_user_id:
            logger.warning(f"Authorization failed: {user_email} tried to approve for {user_id}")
            return jsonify({
                "error": "Forbidden",
                "message": "You can only approve devices for your own instance"
            }), 403

        # 3. Build pod identifiers
        namespace = f"openclaw-{user_id}"
        pod_name = f"openclaw-{user_id}-0"
        container_name = "openclaw"

        # 4. Check if pod exists
        if not check_pod_exists(namespace, pod_name):
            return jsonify({
                "error": "Pod not found",
                "message": f"OpenClaw instance not found"
            }), 404

        # 5. If request_id not provided, auto-find pending request
        if not request_id:
            logger.info(f"🔍 Auto-finding pending device request for user {user_id}")

            # Call list devices command
            list_command = ['openclaw', 'devices', 'list']
            try:
                list_stdout, list_stderr = exec_in_pod(namespace, pod_name, container_name, list_command)
            except Exception as e:
                logger.error(f"❌ Failed to list devices: {str(e)}")
                return jsonify({
                    "error": "Failed to list devices",
                    "message": str(e)
                }), 500

            # Parse output to find pending requests
            # Look for UUID patterns in all lines (not just lines with "pending")
            pending_requests = []
            import re

            # Check if there are any pending requests in the output
            has_pending = 'pending' in list_stdout.lower()

            logger.info(f"Has pending: {has_pending}, stdout length: {len(list_stdout)}")
            logger.info(f"First 300 chars of stdout: {repr(list_stdout[:300])}")

            if has_pending:
                # Extract all UUIDs from the output (they appear in table rows)
                for i, line in enumerate(list_stdout.split('\n')):
                    # Look for UUID pattern anywhere in the line
                    match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', line, re.IGNORECASE)
                    if match:
                        uuid = match.group(1)
                        # Avoid duplicates
                        if uuid not in pending_requests:
                            pending_requests.append(uuid)
                            logger.info(f"✅ Found pending request on line {i}: {uuid}")

            if not pending_requests:
                logger.info(f"ℹ️ No pending device requests for user {user_id}")
                return jsonify({
                    "success": False,
                    "message": "No pending device pairing requests found",
                    "user_id": user_id
                }), 200  # HTTP 200 with success=false

            # Try approving pending requests one by one until one succeeds
            logger.info(f"✅ Found {len(pending_requests)} pending request(s)")

            last_error = None
            for request_id in pending_requests:
                logger.info(f"🔄 Attempting to approve request: {request_id}")

                # 6. Execute device approval command
                command = ['openclaw', 'devices', 'approve', request_id]

                try:
                    stdout, stderr = exec_in_pod(namespace, pod_name, container_name, command)

                    # 7. Check if command succeeded
                    if stderr and 'error' in stderr.lower():
                        logger.warning(f"⚠️ Request {request_id} failed (may be expired): {stderr.strip()}")
                        last_error = stderr.strip()
                        continue  # Try next request

                    # Success!
                    logger.info(f"✅ Device approved: user {user_id}, request_id {request_id}")
                    return jsonify({
                        "success": True,
                        "message": "Device approved successfully",
                        "user_id": user_id,
                        "request_id": request_id,
                        "output": stdout.strip() if stdout else "Device approved"
                    }), 200

                except Exception as e:
                    logger.warning(f"⚠️ Request {request_id} execution failed: {str(e)}")
                    last_error = str(e)
                    continue  # Try next request

            # All requests failed
            logger.error(f"❌ All {len(pending_requests)} pending request(s) failed")
            return jsonify({
                "success": False,
                "error": "All pending requests failed",
                "message": last_error or "All approval attempts failed",
                "attempted_requests": len(pending_requests)
            }), 500

        # 6. Execute device approval command (when request_id is provided)
        command = ['openclaw', 'devices', 'approve', request_id]

        try:
            stdout, stderr = exec_in_pod(namespace, pod_name, container_name, command)
        except Exception as e:
            logger.error(f"❌ Failed to execute device approval: {str(e)}")
            return jsonify({
                "error": "Execution failed",
                "message": str(e)
            }), 500

        # 7. Check if command succeeded
        if stderr and 'error' in stderr.lower():
            logger.error(f"❌ Device approval command error: {stderr}")
            return jsonify({
                "success": False,
                "error": "Approval failed",
                "output": stdout,
                "stderr": stderr
            }), 500

        # 8. Success response
        logger.info(f"✅ Device approved: user {user_id}, request_id {request_id}")
        return jsonify({
            "success": True,
            "message": "Device approved successfully",
            "user_id": user_id,
            "request_id": request_id,
            "output": stdout.strip() if stdout else "Device approved"
        }), 200

    except Exception as e:
        logger.error(f"❌ Error in approve_device: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@devices_bp.route('/api/devices/list', methods=['GET'])
@require_auth
def list_devices():
    """
    List devices for OpenClaw instance

    Query Parameters:
    - user_id: User ID (optional, defaults to authenticated user)

    Response:
    {
        "success": true,
        "devices": [
            {
                "id": "...",
                "name": "...",
                "status": "approved" | "pending"
            }
        ]
    }

    Authorization:
    - Users can only list devices for their own instances
    """
    try:
        # Get authenticated user
        user_email = session['user_email']
        authenticated_user_id = generate_user_id(user_email)

        # 1. Get user_id from query or use authenticated user
        user_id = request.args.get('user_id')
        if not user_id:
            user_id = authenticated_user_id

        # 2. Authorization check
        if user_id != authenticated_user_id:
            logger.warning(f"Authorization failed: user {user_email} tried to list devices for user_id {user_id}")
            return jsonify({
                "error": "Forbidden",
                "message": "You can only list devices for your own instance"
            }), 403

        # 3. Build pod identifiers
        namespace = f"openclaw-{user_id}"
        pod_name = f"openclaw-{user_id}-0"
        container_name = "openclaw"

        # 4. Check if pod exists
        if not check_pod_exists(namespace, pod_name):
            return jsonify({
                "error": "Pod not found",
                "message": f"OpenClaw instance not found"
            }), 404

        # 5. Execute list devices command
        command = ['openclaw', 'devices', 'list']

        try:
            stdout, stderr = exec_in_pod(namespace, pod_name, container_name, command)
        except Exception as e:
            logger.error(f"Failed to list devices: {str(e)}")
            return jsonify({
                "error": "Execution failed",
                "message": str(e)
            }), 500

        # 6. Parse output (assuming JSON output from openclaw devices list)
        # TODO: Parse actual output format from openclaw CLI
        return jsonify({
            "success": True,
            "user_id": user_id,
            "output": stdout.strip() if stdout else ""
        }), 200

    except Exception as e:
        logger.error(f"Error in list_devices: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
