"""
Foundation Stack: VPC + EKS Cluster + EFS + Karpenter + ALB Controller

This stack creates the base infrastructure that all other components
depend on. It takes ~20-30 minutes to deploy (EKS cluster creation
is the bottleneck).
"""

import json
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    Tags,
    aws_ec2 as ec2,
    aws_eks as eks,
    aws_efs as efs,
    aws_iam as iam,
)

from aws_cdk.lambda_layer_kubectl_v32 import KubectlV32Layer

from .config import config


class FoundationStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = config

        # -------------------------------------------------------
        # 1. VPC — Multi-AZ with public/private subnets
        # -------------------------------------------------------
        self.vpc = ec2.Vpc(
            self, "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(cfg.network.vpc_cidr),
            max_azs=cfg.network.max_azs,
            nat_gateways=cfg.network.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=20,
                ),
            ],
        )
        # Tag subnets for ALB controller auto-discovery
        for subnet in self.vpc.public_subnets:
            Tags.of(subnet).add("kubernetes.io/role/elb", "1")
        for subnet in self.vpc.private_subnets:
            Tags.of(subnet).add("kubernetes.io/role/internal-elb", "1")

        # -------------------------------------------------------
        # 2. EKS Cluster — Graviton system nodes
        # -------------------------------------------------------
        # Cluster admin role (for kubectl access)
        self.cluster_admin_role = iam.Role(
            self, "ClusterAdminRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="EKS cluster admin role for kubectl access",
        )

        self.cluster = eks.Cluster(
            self, "Cluster",
            cluster_name=cfg.cluster.name,
            version=eks.KubernetesVersion.of(cfg.cluster.version),
            kubectl_layer=KubectlV32Layer(self, "KubectlLayer"),
            vpc=self.vpc,
            vpc_subnets=[ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            )],
            default_capacity=0,  # We manage node groups ourselves
            masters_role=self.cluster_admin_role,
            endpoint_access=eks.EndpointAccess.PUBLIC_AND_PRIVATE,
            # Auth mode — API + ConfigMap for flexibility
            authentication_mode=eks.AuthenticationMode.API_AND_CONFIG_MAP,
        )

        # System node group — Graviton, runs platform components
        self.system_nodegroup = self.cluster.add_nodegroup_capacity(
            "SystemNodes",
            instance_types=[
                ec2.InstanceType(cfg.cluster.system_node_instance_type),
            ],
            min_size=cfg.cluster.system_node_min,
            max_size=cfg.cluster.system_node_max,
            desired_size=cfg.cluster.system_node_count,
            ami_type=eks.NodegroupAmiType.AL2023_ARM_64_STANDARD,
            capacity_type=eks.CapacityType.ON_DEMAND,
            labels={
                "role": "system",
                "kubernetes.io/arch": "arm64",
            },
            taints=[
                eks.TaintSpec(
                    key="CriticalAddonsOnly",
                    value="true",
                    effect=eks.TaintEffect.PREFER_NO_SCHEDULE,
                ),
            ],
        )

        # -------------------------------------------------------
        # 3. EFS — Shared filesystem with per-tenant isolation
        # -------------------------------------------------------
        self.file_system = efs.FileSystem(
            self, "AgentStorage",
            vpc=self.vpc,
            encrypted=cfg.storage.encrypted,
            throughput_mode=efs.ThroughputMode.ELASTIC,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            removal_policy=RemovalPolicy.DESTROY,  # PoC: clean up on destroy
            lifecycle_policy=efs.LifecyclePolicy.AFTER_30_DAYS,
        )
        # Allow EKS nodes to mount EFS
        self.file_system.connections.allow_default_port_from(
            self.cluster.connections,
        )

        # Install EFS CSI Driver as EKS Addon
        efs_csi_addon = eks.CfnAddon(
            self, "EfsCsiAddon",
            addon_name="aws-efs-csi-driver",
            cluster_name=self.cluster.cluster_name,
            resolve_conflicts="OVERWRITE",
        )

        # EFS StorageClass for dynamic provisioning
        self.cluster.add_manifest("EfsStorageClass", {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "StorageClass",
            "metadata": {
                "name": "efs-sc",
            },
            "provisioner": "efs.csi.aws.com",
            "parameters": {
                "provisioningMode": "efs-ap",  # EFS Access Point per PVC
                "fileSystemId": self.file_system.file_system_id,
                "directoryPerms": "700",
                "uid": "1000",  # OpenClaw runs as UID 1000
                "gid": "1000",
                "basePath": "/openclaw-agents",
            },
            "reclaimPolicy": "Delete",
            "volumeBindingMode": "Immediate",
        })

        # -------------------------------------------------------
        # 4. EKS Pod Identity Agent — for Bedrock access
        # -------------------------------------------------------
        eks.CfnAddon(
            self, "PodIdentityAddon",
            addon_name="eks-pod-identity-agent",
            cluster_name=self.cluster.cluster_name,
            resolve_conflicts="OVERWRITE",
        )

        # -------------------------------------------------------
        # 5. Karpenter — Autoscaler for agent workloads
        # -------------------------------------------------------
        self._setup_karpenter(cfg)

        # -------------------------------------------------------
        # 6. AWS Load Balancer Controller — for ALB Ingress
        # -------------------------------------------------------
        self._setup_alb_controller()

        # -------------------------------------------------------
        # Outputs
        # -------------------------------------------------------
        CfnOutput(self, "ClusterName",
                  value=self.cluster.cluster_name,
                  description="EKS cluster name")
        CfnOutput(self, "ClusterEndpoint",
                  value=self.cluster.cluster_endpoint,
                  description="EKS API endpoint")
        CfnOutput(self, "EfsFileSystemId",
                  value=self.file_system.file_system_id,
                  description="EFS filesystem ID for agent storage")
        CfnOutput(self, "KubeconfigCommand",
                  value=f"aws eks update-kubeconfig --name {cfg.cluster.name} "
                        f"--region {cfg.region} "
                        f"--role-arn {self.cluster_admin_role.role_arn}",
                  description="Command to configure kubectl")

    # -----------------------------------------------------------
    # Karpenter setup
    # -----------------------------------------------------------
    def _setup_karpenter(self, cfg):
        """Install Karpenter and configure IAM for node provisioning."""

        # IAM Role for Karpenter controller
        karpenter_ns = "kube-system"
        karpenter_sa = "karpenter"

        karpenter_controller_role = iam.Role(
            self, "KarpenterControllerRole",
            assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
            description="Karpenter controller role for node provisioning",
        )

        # Karpenter needs EC2, pricing, SSM, EKS permissions
        karpenter_controller_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                # EC2 — launch and manage instances
                "ec2:CreateFleet",
                "ec2:CreateLaunchTemplate",
                "ec2:CreateTags",
                "ec2:DeleteLaunchTemplate",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceTypeOfferings",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeLaunchTemplates",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSubnets",
                "ec2:RunInstances",
                "ec2:TerminateInstances",
                # Pricing — for cost-aware scheduling
                "pricing:GetProducts",
                # SSM — for AMI discovery
                "ssm:GetParameter",
                # EKS — for cluster info
                "eks:DescribeCluster",
            ],
            resources=["*"],
        ))

        # IAM Role for Karpenter-managed nodes
        self.karpenter_node_role = iam.Role(
            self, "KarpenterNodeRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"),
            ],
        )

        # Allow Karpenter to pass the node role
        karpenter_controller_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["iam:PassRole"],
            resources=[self.karpenter_node_role.role_arn],
        ))

        # Instance profile for Karpenter nodes
        instance_profile = iam.CfnInstanceProfile(
            self, "KarpenterNodeInstanceProfile",
            roles=[self.karpenter_node_role.role_name],
            instance_profile_name=f"{cfg.cluster.name}-karpenter-node",
        )

        # Map Karpenter node role to aws-auth ConfigMap
        self.cluster.aws_auth.add_role_mapping(
            self.karpenter_node_role,
            groups=["system:bootstrappers", "system:nodes"],
            username="system:node:{{EC2PrivateDNSName}}",
        )

        # Pod Identity association for Karpenter
        eks.CfnPodIdentityAssociation(
            self, "KarpenterPodIdentity",
            cluster_name=self.cluster.cluster_name,
            namespace=karpenter_ns,
            service_account=karpenter_sa,
            role_arn=karpenter_controller_role.role_arn,
        )

        # Install Karpenter via Helm
        self.cluster.add_helm_chart(
            "Karpenter",
            chart="karpenter",
            repository="oci://public.ecr.aws/karpenter",
            version=cfg.karpenter.version,
            namespace=karpenter_ns,
            values={
                "settings": {
                    "clusterName": self.cluster.cluster_name,
                    "clusterEndpoint": self.cluster.cluster_endpoint,
                },
                "tolerations": [
                    {
                        "key": "CriticalAddonsOnly",
                        "operator": "Exists",
                    },
                ],
                "nodeSelector": {
                    "role": "system",
                },
            },
        )

        # Export values needed by Karpenter manifests
        self._karpenter_node_role_name = self.karpenter_node_role.role_name
        self._karpenter_instance_profile_name = instance_profile.instance_profile_name

        CfnOutput(self, "KarpenterNodeRoleName",
                  value=self.karpenter_node_role.role_name)
        CfnOutput(self, "KarpenterInstanceProfileName",
                  value=instance_profile.instance_profile_name or "")

    # -----------------------------------------------------------
    # AWS Load Balancer Controller setup
    # -----------------------------------------------------------
    def _setup_alb_controller(self):
        """Install AWS Load Balancer Controller for ALB Ingress."""

        alb_ns = "kube-system"
        alb_sa = "aws-load-balancer-controller"

        alb_role = iam.Role(
            self, "AlbControllerRole",
            assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
            description="AWS Load Balancer Controller role",
        )

        # Full ALB Controller IAM policy
        # Reference: https://docs.aws.amazon.com/eks/latest/userguide/lbc-manifest.html
        alb_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "acm:DescribeCertificate",
                "acm:ListCertificates",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateSecurityGroup",
                "ec2:CreateTags",
                "ec2:DeleteSecurityGroup",
                "ec2:DeleteTags",
                "ec2:Describe*",
                "ec2:RevokeSecurityGroupIngress",
                "elasticloadbalancing:*",
                "iam:CreateServiceLinkedRole",
                "shield:GetSubscriptionState",
                "waf-regional:*",
                "wafv2:*",
                "tag:GetResources",
                "tag:TagResources",
            ],
            resources=["*"],
        ))

        # Pod Identity association
        eks.CfnPodIdentityAssociation(
            self, "AlbControllerPodIdentity",
            cluster_name=self.cluster.cluster_name,
            namespace=alb_ns,
            service_account=alb_sa,
            role_arn=alb_role.role_arn,
        )

        # Install via Helm
        self.cluster.add_helm_chart(
            "AlbController",
            chart="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            namespace=alb_ns,
            values={
                "clusterName": self.cluster.cluster_name,
                "serviceAccount": {
                    "create": True,
                    "name": alb_sa,
                },
                "nodeSelector": {
                    "role": "system",
                },
                "tolerations": [
                    {
                        "key": "CriticalAddonsOnly",
                        "operator": "Exists",
                    },
                ],
            },
        )
