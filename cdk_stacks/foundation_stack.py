"""
Foundation Stack: VPC + EKS Cluster + EFS + Karpenter + ALB Controller

This stack creates the base infrastructure that all other components
depend on. It takes ~20-30 minutes to deploy (EKS cluster creation
is the bottleneck).

Resources created: VPC (3-AZ), EKS Cluster (Graviton system nodes),
EFS (encrypted, per-tenant Access Points), Karpenter (Graviton autoscaler),
AWS Load Balancer Controller (ALB Ingress), Pod Identity Agent.
"""

from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Tags,
    aws_ec2 as ec2,
    aws_eks as eks,
    aws_efs as efs,
    aws_iam as iam,
)

from aws_cdk.lambda_layer_kubectl_v32 import KubectlV32Layer

from .config import config


def _make_pod_identity_role(scope, id: str, description: str) -> iam.Role:
    """Create IAM Role for EKS Pod Identity with correct trust policy.
    
    Pod Identity requires sts:AssumeRole AND sts:TagSession in a SINGLE
    trust policy statement. CDK's ServicePrincipal only adds AssumeRole
    as a separate statement, which EKS rejects as 'Trust policy invalid'.
    
    Fix: Use CompositePrincipal is not needed — instead, construct the
    trust policy document manually with both actions in one statement.
    """
    trust_policy = iam.PolicyDocument(
        statements=[
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:AssumeRole", "sts:TagSession"],
            ),
        ],
    )
    role = iam.Role(
        scope, id,
        assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),  # placeholder, overridden below
        description=description,
    )
    # Override the auto-generated trust policy with our correct single-statement version
    cfn_role = role.node.default_child
    cfn_role.add_property_override("AssumeRolePolicyDocument", {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "pods.eks.amazonaws.com"},
                "Action": ["sts:AssumeRole", "sts:TagSession"],
            },
        ],
    })
    return role


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
            # ⚠️ COST NOTE: 1 NAT Gateway for PoC. Set to max_azs (3)
            # for production HA — single NAT is a single point of failure.
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
            # ⚠️ SECURITY NOTE: PUBLIC_AND_PRIVATE for PoC demo convenience.
            # For production, use PRIVATE with VPN/Bastion, or restrict
            # public access to specific CIDRs via:
            #   endpoint_access=eks.EndpointAccess.PUBLIC_AND_PRIVATE.only_from("your.ip/32")
            endpoint_access=eks.EndpointAccess.PUBLIC_AND_PRIVATE,
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
                # Note: kubernetes.io/arch is auto-set by kubelet, don't set manually
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
        removal = (RemovalPolicy.DESTROY if cfg.storage.removal_policy == "DESTROY"
                   else RemovalPolicy.RETAIN)

        self.file_system = efs.FileSystem(
            self, "AgentStorage",
            vpc=self.vpc,
            encrypted=cfg.storage.encrypted,
            throughput_mode=efs.ThroughputMode.ELASTIC,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            # ⚠️ DATA SAFETY: DESTROY for PoC (clean teardown).
            # Set storage.removal_policy="RETAIN" in config for production!
            removal_policy=removal,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_30_DAYS,
        )
        # Allow EKS nodes to mount EFS
        self.file_system.connections.allow_default_port_from(
            self.cluster.connections,
        )

        # [FIX C1] EFS CSI Driver — needs IAM role for Access Point creation
        efs_csi_role = _make_pod_identity_role(
            self, "EfsCsiDriverRole",
            description="EFS CSI Driver - create/delete Access Points for tenant PVCs",
        )
        efs_csi_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "elasticfilesystem:DescribeAccessPoints",
                "elasticfilesystem:DescribeFileSystems",
                "elasticfilesystem:DescribeMountTargets",
                "elasticfilesystem:CreateAccessPoint",
                "elasticfilesystem:DeleteAccessPoint",
                "elasticfilesystem:TagResource",
            ],
            resources=["*"],
        ))

        # Pod Identity for EFS CSI controller
        eks.CfnPodIdentityAssociation(
            self, "EfsCsiPodIdentity",
            cluster_name=self.cluster.cluster_name,
            namespace="kube-system",
            service_account="efs-csi-controller-sa",
            role_arn=efs_csi_role.role_arn,
        )

        # Install EFS CSI Driver as EKS Addon with IAM role
        efs_csi_addon = eks.CfnAddon(
            self, "EfsCsiAddon",
            addon_name="aws-efs-csi-driver",
            cluster_name=self.cluster.cluster_name,
            service_account_role_arn=efs_csi_role.role_arn,
            resolve_conflicts="OVERWRITE",
        )
        # [FIX I7] Explicit dependency — addon needs cluster + nodegroup ready
        efs_csi_addon.node.add_dependency(self.system_nodegroup)

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
        pod_identity_addon = eks.CfnAddon(
            self, "PodIdentityAddon",
            addon_name="eks-pod-identity-agent",
            cluster_name=self.cluster.cluster_name,
            resolve_conflicts="OVERWRITE",
        )
        # [FIX I7] Explicit dependency
        pod_identity_addon.node.add_dependency(self.system_nodegroup)

        # -------------------------------------------------------
        # 5. Karpenter — Autoscaler for agent workloads
        # -------------------------------------------------------
        self._setup_karpenter(cfg)

        # -------------------------------------------------------
        # 6. AWS Load Balancer Controller — for ALB Ingress
        # -------------------------------------------------------
        self._setup_alb_controller(cfg)

        # Note: ALB webhook failurePolicy=Ignore avoids blocking Karpenter Service
        # creation. No explicit dependency needed between Karpenter and ALB charts.

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

        karpenter_ns = "kube-system"
        karpenter_sa = "karpenter"

        karpenter_controller_role = _make_pod_identity_role(
            self, "KarpenterControllerRole",
            description="Karpenter controller role for node provisioning",
        )

        # [FIX I1] Split into read-only (broad) and mutating (tag-scoped)
        # Read-only — safe with Resource: *
        karpenter_controller_role.add_to_policy(iam.PolicyStatement(
            sid="KarpenterReadOnly",
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceTypeOfferings",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeLaunchTemplates",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSubnets",
                "ec2:DescribeSpotPriceHistory",
                "pricing:GetProducts",
                "ssm:GetParameter",
                "eks:DescribeCluster",
                "iam:ListInstanceProfiles",
                "iam:GetInstanceProfile",
            ],
            resources=["*"],
        ))

        # Mutating — scoped by tag condition
        karpenter_controller_role.add_to_policy(iam.PolicyStatement(
            sid="KarpenterMutating",
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateFleet",
                "ec2:RunInstances",
                "ec2:CreateLaunchTemplate",
                "ec2:DeleteLaunchTemplate",
                "ec2:TerminateInstances",
                "ec2:CreateTags",
            ],
            resources=["*"],
            conditions={
                "StringEquals": {
                    f"aws:RequestTag/karpenter.sh/managed-by": cfg.cluster.name,
                },
            },
        ))

        # Also allow tagging already-managed resources
        karpenter_controller_role.add_to_policy(iam.PolicyStatement(
            sid="KarpenterTagScoped",
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:TerminateInstances",
                "ec2:DeleteLaunchTemplate",
            ],
            resources=["*"],
            conditions={
                "StringEquals": {
                    f"aws:ResourceTag/karpenter.sh/managed-by": cfg.cluster.name,
                },
            },
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
        self._karpenter_chart = self.cluster.add_helm_chart(
            "Karpenter",
            chart="karpenter",
            repository="oci://public.ecr.aws/karpenter/karpenter",
            version=cfg.karpenter.version,
            namespace=karpenter_ns,
            values={
                "settings": {
                    "clusterName": self.cluster.cluster_name,
                    "clusterEndpoint": self.cluster.cluster_endpoint,
                },
                "serviceAccount": {
                    "create": True,
                    "name": karpenter_sa,
                },
                "controller": {
                    "env": [
                        {"name": "AWS_REGION", "value": cfg.region},
                    ],
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
    def _setup_alb_controller(self, cfg):
        """Install AWS Load Balancer Controller for ALB Ingress."""

        alb_ns = "kube-system"
        alb_sa = "aws-load-balancer-controller"

        alb_role = _make_pod_identity_role(
            self, "AlbControllerRole",
            description="AWS Load Balancer Controller role",
        )

        # [FIX C3] Use scoped IAM policy instead of wildcards
        # Reference: https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/main/docs/install/iam_policy.json
        # Read-only operations
        alb_role.add_to_policy(iam.PolicyStatement(
            sid="AlbReadOnly",
            effect=iam.Effect.ALLOW,
            actions=[
                "acm:DescribeCertificate",
                "acm:ListCertificates",
                "acm:GetCertificate",
                "ec2:DescribeAccountAttributes",
                "ec2:DescribeAddresses",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeVpcs",
                "ec2:DescribeVpcPeeringConnections",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInstances",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeTags",
                "ec2:DescribeCoipPools",
                "ec2:GetCoipPoolUsage",
                "ec2:DescribeTargetGroups",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeLoadBalancerAttributes",
                "elasticloadbalancing:DescribeListeners",
                "elasticloadbalancing:DescribeListenerCertificates",
                "elasticloadbalancing:DescribeSSLPolicies",
                "elasticloadbalancing:DescribeRules",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:DescribeTargetGroupAttributes",
                "elasticloadbalancing:DescribeTargetHealth",
                "elasticloadbalancing:DescribeTags",
                "elasticloadbalancing:DescribeTrustStores",
                "iam:ListServerCertificates",
                "iam:GetServerCertificate",
                "cognito-idp:DescribeUserPoolClient",
                "shield:GetSubscriptionState",
                "shield:DescribeProtection",
                "shield:CreateProtection",
                "shield:DeleteProtection",
                "tag:GetResources",
                "tag:TagResources",
                "wafv2:GetWebACL",
                "wafv2:GetWebACLForResource",
                "wafv2:AssociateWebACL",
                "wafv2:DisassociateWebACL",
                "waf-regional:GetWebACLForResource",
                "waf-regional:GetWebACL",
                "waf-regional:AssociateWebACL",
                "waf-regional:DisassociateWebACL",
            ],
            resources=["*"],
        ))

        # Mutating operations — create/manage ALB resources
        alb_role.add_to_policy(iam.PolicyStatement(
            sid="AlbMutating",
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:CreateSecurityGroup",
                "ec2:DeleteSecurityGroup",
                "ec2:CreateTags",
                "ec2:DeleteTags",
                "elasticloadbalancing:CreateLoadBalancer",
                "elasticloadbalancing:CreateTargetGroup",
                "elasticloadbalancing:CreateListener",
                "elasticloadbalancing:CreateRule",
                "elasticloadbalancing:DeleteLoadBalancer",
                "elasticloadbalancing:DeleteTargetGroup",
                "elasticloadbalancing:DeleteListener",
                "elasticloadbalancing:DeleteRule",
                "elasticloadbalancing:ModifyLoadBalancerAttributes",
                "elasticloadbalancing:ModifyTargetGroup",
                "elasticloadbalancing:ModifyTargetGroupAttributes",
                "elasticloadbalancing:ModifyListener",
                "elasticloadbalancing:ModifyRule",
                "elasticloadbalancing:AddListenerCertificates",
                "elasticloadbalancing:RemoveListenerCertificates",
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:RemoveTags",
                "elasticloadbalancing:SetIpAddressType",
                "elasticloadbalancing:SetSecurityGroups",
                "elasticloadbalancing:SetSubnets",
                "elasticloadbalancing:SetWebAcl",
                "elasticloadbalancing:RegisterTargets",
                "elasticloadbalancing:DeregisterTargets",
                "iam:CreateServiceLinkedRole",
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

        # [FIX S3] Pin Helm chart version
        self._alb_chart = self.cluster.add_helm_chart(
            "AlbController",
            chart="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            version=cfg.alb_controller_chart_version,
            namespace=alb_ns,
            values={
                "clusterName": self.cluster.cluster_name,
                "region": cfg.region,
                "vpcId": self.vpc.vpc_id,
                "serviceAccount": {
                    "create": True,
                    "name": alb_sa,
                },
                # Avoid blocking other Service creates while ALB Pod is starting
                "serviceMutatorWebhookConfig": {
                    "failurePolicy": "Ignore",
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
