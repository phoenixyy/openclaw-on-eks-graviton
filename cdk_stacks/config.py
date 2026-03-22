"""
Centralized configuration for the OpenClaw on EKS Graviton project.
All tunable parameters live here — change these values to adapt
the deployment to a different region, scale, or cost target.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ClusterConfig:
    """EKS cluster settings."""
    name: str = "openclaw-platform"
    version: str = "1.32"
    # System node group — runs Operator, Karpenter, Provisioning Service
    system_node_instance_type: str = "c6g.large"
    system_node_count: int = 2
    system_node_min: int = 2
    system_node_max: int = 4


@dataclass
class KarpenterConfig:
    """Karpenter autoscaler settings for OpenClaw agent workloads."""
    version: str = "1.3.0"
    # Instance families Karpenter can choose from (all Graviton)
    instance_families: List[str] = field(default_factory=lambda: [
        "c6g", "c7g",   # Compute-optimized Graviton
        "m6g", "m7g",   # General-purpose Graviton (fallback)
    ])
    instance_sizes: List[str] = field(default_factory=lambda: [
        "large", "xlarge", "2xlarge",
    ])
    # Consolidation — aggressively pack pods to save cost
    consolidation_policy: str = "WhenEmptyOrUnderutilized"
    consolidate_after: str = "60s"
    # Spot vs On-Demand ratio
    capacity_types: List[str] = field(default_factory=lambda: [
        "spot", "on-demand",
    ])


@dataclass
class StorageConfig:
    """EFS settings for agent workspace persistence."""
    # Per-tenant PVC size (default for new OpenClawInstance)
    default_pvc_size_gi: int = 10
    # EFS throughput mode
    throughput_mode: str = "elastic"
    # Encryption at rest
    encrypted: bool = True
    # ⚠️ DESTROY for PoC (clean teardown). Set to "RETAIN" for production!
    removal_policy: str = "DESTROY"


@dataclass
class NetworkConfig:
    """VPC and networking settings."""
    vpc_cidr: str = "10.0.0.0/16"
    max_azs: int = 3
    nat_gateways: int = 1  # Cost optimization: 1 NAT for PoC, 3 for prod


@dataclass
class BedrockConfig:
    """Bedrock model access configuration."""
    # Default model for new agent instances
    default_model: str = "anthropic/claude-sonnet-4-20250514"
    # Region for Bedrock API calls (can differ from EKS region)
    region: str = "ap-northeast-1"


@dataclass
class OperatorConfig:
    """OpenClaw Kubernetes Operator settings."""
    chart_version: str = "0.22.2"
    chart_repository: str = "oci://ghcr.io/openclaw-rocks/charts/openclaw-operator"
    namespace: str = "openclaw-operator-system"


@dataclass
class PlatformConfig:
    """Top-level configuration aggregating all sub-configs."""
    project_name: str = "openclaw-platform"
    region: str = "ap-northeast-1"
    # Sub-configs
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    karpenter: KarpenterConfig = field(default_factory=KarpenterConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    # Helm chart versions (pinned for reproducibility)
    alb_controller_chart_version: str = "1.12.0"
    # Tags applied to all resources
    tags: dict = field(default_factory=lambda: {
        "Project": "openclaw-on-eks-graviton",
        "ManagedBy": "CDK",
        "Environment": "poc",
    })


# Singleton config — import this in stacks
config = PlatformConfig()
