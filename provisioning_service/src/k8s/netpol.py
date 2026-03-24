"""NetworkPolicy operations"""
from kubernetes import client
from app.config import Config
import logging

logger = logging.getLogger(__name__)

def create_network_policy(k8s_client, namespace):
    """
    Create a NetworkPolicy in the namespace

    Args:
        k8s_client: K8sClient instance
        namespace: Namespace name

    Returns:
        Tuple of (netpol, created)
    """
    netpol = client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(name="openclaw-netpol"),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(
                match_labels={"app.kubernetes.io/component": "openclaw"}
            ),
            policy_types=["Ingress", "Egress"],
            ingress=[
                client.V1NetworkPolicyIngressRule(
                    _from=[
                        client.V1NetworkPolicyPeer(
                            namespace_selector=client.V1LabelSelector(
                                match_labels={"kubernetes.io/metadata.name": "ingress-nginx"}
                            )
                        )
                    ],
                    ports=[
                        client.V1NetworkPolicyPort(protocol="TCP", port=18789)
                    ]
                )
            ],
            egress=[
                # DNS
                client.V1NetworkPolicyEgressRule(
                    to=[client.V1NetworkPolicyPeer(namespace_selector=client.V1LabelSelector())],
                    ports=[
                        client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        client.V1NetworkPolicyPort(protocol="UDP", port=53)
                    ]
                ),
                # HTTPS
                client.V1NetworkPolicyEgressRule(
                    to=[client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr="0.0.0.0/0"))],
                    ports=[client.V1NetworkPolicyPort(protocol="TCP", port=443)]
                )
            ]
        )
    )

    def create():
        return k8s_client.networking_v1.create_namespaced_network_policy(
            namespace=namespace,
            body=netpol
        )

    def get():
        return k8s_client.networking_v1.read_namespaced_network_policy(
            name="openclaw-netpol",
            namespace=namespace
        )

    result = k8s_client.create_or_get(create, get, f"NetworkPolicy in {namespace}")

    # Create additional ALB ingress NetworkPolicy
    # This is separate from the Operator-managed policy to survive reconciliation.
    # Allows ALB health checks and traffic from within VPC to reach the pod.
    _create_alb_ingress_policy(k8s_client, namespace)

    return result


def _create_alb_ingress_policy(k8s_client, namespace):
    """
    Create an additional NetworkPolicy to allow ALB (VPC internal) traffic.

    The OpenClaw Operator manages its own NetworkPolicy and will overwrite
    any ipBlock rules we add to it. This separate policy survives reconciliation
    because the Operator only manages objects with its ownerReference.
    """
    vpc_cidr = Config.VPC_CIDR

    netpol = client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(
            name="allow-alb-ingress",
            labels={
                "app.kubernetes.io/managed-by": "openclaw-provisioning-service",
                "purpose": "allow-internal-alb-traffic",
            },
        ),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(
                match_labels={"app.kubernetes.io/name": "openclaw"}
            ),
            policy_types=["Ingress"],
            ingress=[
                client.V1NetworkPolicyIngressRule(
                    _from=[
                        client.V1NetworkPolicyPeer(
                            ip_block=client.V1IPBlock(cidr=vpc_cidr)
                        )
                    ],
                    ports=[
                        client.V1NetworkPolicyPort(protocol="TCP", port=18789),
                        client.V1NetworkPolicyPort(protocol="TCP", port=18793),
                    ]
                )
            ]
        )
    )

    def create():
        return k8s_client.networking_v1.create_namespaced_network_policy(
            namespace=namespace,
            body=netpol
        )

    def get():
        return k8s_client.networking_v1.read_namespaced_network_policy(
            name="allow-alb-ingress",
            namespace=namespace
        )

    k8s_client.create_or_get(create, get, f"ALB Ingress NetworkPolicy in {namespace}")
