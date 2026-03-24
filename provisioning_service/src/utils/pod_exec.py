"""Kubernetes Pod exec utilities"""
from kubernetes import client
from kubernetes.stream import stream
import logging

logger = logging.getLogger(__name__)


def exec_in_pod(namespace, pod_name, container_name, command):
    """
    Execute a command in a pod container using kubectl exec

    Args:
        namespace: Namespace name
        pod_name: Pod name
        container_name: Container name
        command: Command to execute (list of strings, e.g., ['openclaw', 'devices', 'approve', 'request_id'])

    Returns:
        Tuple of (stdout, stderr)

    Raises:
        Exception: If exec fails
    """
    core_v1 = client.CoreV1Api()

    try:
        logger.info(f"Executing command in pod {namespace}/{pod_name} container {container_name}: {' '.join(command)}")

        # Execute command using stream API
        resp = stream(
            core_v1.connect_get_namespaced_pod_exec,
            name=pod_name,
            namespace=namespace,
            container=container_name,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False
        )

        # Read output with increased timeout to handle table output
        stdout = ""
        stderr = ""

        import time
        max_wait = 10  # Maximum 10 seconds to wait for output
        start_time = time.time()

        while resp.is_open():
            resp.update(timeout=3)  # Increased from 1 to 3 seconds
            if resp.peek_stdout():
                stdout += resp.read_stdout()
            if resp.peek_stderr():
                stderr += resp.read_stderr()

            # Break if we've waited too long
            if time.time() - start_time > max_wait:
                break

        # Read any remaining output after stream closes
        if resp.peek_stdout():
            stdout += resp.read_stdout()
        if resp.peek_stderr():
            stderr += resp.read_stderr()

        resp.close()

        logger.info(f"Command executed successfully. stdout length: {len(stdout)}, stderr length: {len(stderr)}")

        return stdout, stderr

    except Exception as e:
        logger.error(f"Failed to exec in pod {namespace}/{pod_name}: {str(e)}")
        raise


def check_pod_exists(namespace, pod_name):
    """
    Check if a pod exists

    Args:
        namespace: Namespace name
        pod_name: Pod name

    Returns:
        Boolean: True if pod exists, False otherwise
    """
    core_v1 = client.CoreV1Api()

    try:
        core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        return True
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return False
        raise
