"""Kubernetes client wrapper"""
from kubernetes import client
from kubernetes.client.rest import ApiException
import logging

logger = logging.getLogger(__name__)

class K8sClient:
    """Kubernetes client wrapper with helper methods"""

    def __init__(self):
        self.core_v1 = client.CoreV1Api()
        self.networking_v1 = client.NetworkingV1Api()
        self.custom_objects = client.CustomObjectsApi()
        self.apps_v1 = client.AppsV1Api()

    def create_or_get(self, create_func, get_func, resource_name, *args, **kwargs):
        """
        Idempotent resource creation: create if not exists, otherwise return existing

        Args:
            create_func: Function to create resource
            get_func: Function to get resource
            resource_name: Resource name for logging
            *args, **kwargs: Arguments to pass to functions

        Returns:
            Tuple of (resource, created) where created is True if newly created
        """
        try:
            result = create_func(*args, **kwargs)
            logger.info(f"✅ Created {resource_name}")
            return result, True
        except ApiException as e:
            if e.status == 409:  # Conflict - resource already exists
                logger.info(f"⚠️  {resource_name} already exists, fetching...")
                result = get_func(*args, **kwargs)
                return result, False
            else:
                logger.error(f"❌ Error creating {resource_name}: {e}")
                raise

    def wait_for_pod_ready(self, namespace, label_selector, timeout=300):
        """
        Wait for Pod to become Ready (optional, for synchronous creation)

        Args:
            namespace: Namespace name
            label_selector: Label selector (e.g., "app.kubernetes.io/instance=openclaw-xxx")
            timeout: Timeout in seconds

        Returns:
            True if Pod is ready, False if timeout
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                pods = self.core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=label_selector
                )

                if not pods.items:
                    logger.debug(f"⏳ No pods found with selector {label_selector}")
                    time.sleep(5)
                    continue

                # Check if all Pods are Ready
                all_ready = True
                for pod in pods.items:
                    if pod.status.phase != 'Running':
                        all_ready = False
                        break

                    # Check container readiness
                    if pod.status.container_statuses:
                        for container in pod.status.container_statuses:
                            if not container.ready:
                                all_ready = False
                                break

                if all_ready:
                    logger.info(f"✅ Pod(s) ready in {namespace}")
                    return True

                time.sleep(5)

            except Exception as e:
                logger.error(f"❌ Error checking pod status: {e}")
                time.sleep(5)

        logger.warning(f"⏱️  Timeout waiting for pod in {namespace}")
        return False
