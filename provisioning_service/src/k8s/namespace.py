"""Namespace operations"""
from kubernetes import client
import logging

logger = logging.getLogger(__name__)

def create_namespace(k8s_client, instance_id, user_id=None):
    """
    Create a Namespace for the instance

    Args:
        k8s_client: K8sClient instance
        instance_id: Instance ID (e.g. '08c2423c' or '08c2423c-02')
        user_id: User ID for labeling (defaults to instance_id for backward compat)

    Returns:
        Tuple of (namespace, created)
    """
    if user_id is None:
        user_id = instance_id
    namespace_name = f"openclaw-{instance_id}"

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
