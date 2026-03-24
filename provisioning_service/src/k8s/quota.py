"""ResourceQuota operations"""
from kubernetes import client
from app.config import Config
import logging

logger = logging.getLogger(__name__)

def create_resource_quota(k8s_client, namespace):
    """
    Create a ResourceQuota in the namespace

    Args:
        k8s_client: K8sClient instance
        namespace: Namespace name

    Returns:
        Tuple of (quota, created)
    """
    quota = client.V1ResourceQuota(
        metadata=client.V1ObjectMeta(name="openclaw-quota"),
        spec=client.V1ResourceQuotaSpec(
            hard=Config.RESOURCE_QUOTA
        )
    )

    def create():
        return k8s_client.core_v1.create_namespaced_resource_quota(
            namespace=namespace,
            body=quota
        )

    def get():
        return k8s_client.core_v1.read_namespaced_resource_quota(
            name="openclaw-quota",
            namespace=namespace
        )

    return k8s_client.create_or_get(create, get, f"ResourceQuota in {namespace}")
