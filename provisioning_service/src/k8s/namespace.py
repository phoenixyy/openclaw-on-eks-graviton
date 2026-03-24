"""Namespace operations"""
from kubernetes import client
import logging

logger = logging.getLogger(__name__)

def create_namespace(k8s_client, user_id):
    """
    Create a Namespace for the user

    Args:
        k8s_client: K8sClient instance
        user_id: User ID

    Returns:
        Tuple of (namespace, created)
    """
    namespace_name = f"openclaw-{user_id}"

    namespace = client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=namespace_name,
            labels={
                "app.kubernetes.io/managed-by": "openclaw-provisioning",
                "openclaw.rocks/user-id": user_id
            }
        )
    )

    def create():
        return k8s_client.core_v1.create_namespace(body=namespace)

    def get():
        return k8s_client.core_v1.read_namespace(name=namespace_name)

    return k8s_client.create_or_get(create, get, f"Namespace {namespace_name}")
