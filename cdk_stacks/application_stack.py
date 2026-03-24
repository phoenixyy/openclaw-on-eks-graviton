"""
Application Stack: OpenClaw Operator + Provisioning Service + PostgreSQL + ALB Ingress

This stack deploys the application layer on top of the Foundation Stack.
It requires a running EKS cluster with Karpenter, EFS, and ALB Controller.

Resources created:
- OpenClaw Operator (Helm chart, OCI)
- Bedrock IAM Role (Pod Identity template for agent instances)
- PostgreSQL (StatefulSet with gp2 PVC)
- Provisioning Service (Deployment + RBAC + ConfigMap)
- ALB Ingress (internet-facing, shared ALB group)

⚠️ CDK Cross-Stack Note:
  When ApplicationStack references FoundationStack.cluster, calling
  cluster.add_manifest() creates constructs INSIDE FoundationStack.
  If those manifests reference resources from ApplicationStack (like
  bedrock_role.role_arn), CDK sees a cyclic dependency:
    ApplicationStack → FoundationStack (explicit dependency)
    FoundationStack → ApplicationStack (via cross-stack token)
  
  Fix: Use eks.KubernetesManifest() directly in this stack for manifests
  that reference ApplicationStack-owned resources. Use cluster.add_manifest()
  only for manifests with no cross-stack references, or use
  cluster.add_helm_chart() which handles this correctly.
"""

from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_eks as eks,
    aws_ec2 as ec2,
    aws_efs as efs,
    aws_iam as iam,
)

from .config import config


# Re-use the same Pod Identity role helper from foundation_stack
def _make_pod_identity_role(scope, id: str, description: str) -> iam.Role:
    """Create IAM Role for EKS Pod Identity with correct trust policy."""
    role = iam.Role(
        scope, id,
        assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
        description=description,
    )
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


# ── Constants ──────────────────────────────────────────────────
PROVISIONING_NS = "openclaw-provisioning"
PROVISIONING_SA = "openclaw-provisioner"
PROVISIONING_IMAGE = "public.ecr.aws/u6t0z4w2/openclaw-provisioning-chinaregion:latest"
ALB_GROUP_NAME = "openclaw-shared-alb"


class ApplicationStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster: eks.Cluster,
        vpc: ec2.Vpc,
        efs_file_system_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = config
        self.cluster = cluster

        # -------------------------------------------------------
        # 1. OpenClaw Operator — Helm chart (OCI)
        # -------------------------------------------------------
        self._setup_operator(cfg)

        # -------------------------------------------------------
        # 2. Bedrock IAM Role (template for agent Pod Identity)
        # -------------------------------------------------------
        self._setup_bedrock_role(cfg)

        # -------------------------------------------------------
        # 3. Provisioning Namespace + PostgreSQL
        # -------------------------------------------------------
        ns_manifest = self._create_provisioning_namespace()
        self._setup_postgres(ns_manifest)

        # -------------------------------------------------------
        # 4. Provisioning Service IAM Role (Pod Identity)
        # -------------------------------------------------------
        self._setup_provisioning_iam(cfg)

        # -------------------------------------------------------
        # 5. Provisioning Service (RBAC + ConfigMap + Deployment)
        # -------------------------------------------------------
        self._setup_provisioning_service(cfg, efs_file_system_id, ns_manifest)

        # -------------------------------------------------------
        # 6. ALB Ingress (internet-facing)
        # -------------------------------------------------------
        self._setup_alb_ingress(ns_manifest)

    # -----------------------------------------------------------
    # Helper: add K8s manifest without cross-stack cycle
    # -----------------------------------------------------------
    def _add_manifest(self, id: str, *manifests) -> eks.KubernetesManifest:
        """Add K8s manifest as a child of THIS stack (not FoundationStack).

        Uses eks.KubernetesManifest directly instead of cluster.add_manifest()
        to avoid cyclic cross-stack references when manifests contain tokens
        that resolve to ApplicationStack resources.
        """
        return eks.KubernetesManifest(
            self, id,
            cluster=self.cluster,
            manifest=list(manifests),
        )

    # -----------------------------------------------------------
    # 1. OpenClaw Operator
    # -----------------------------------------------------------
    def _setup_operator(self, cfg):
        """Install OpenClaw Operator via OCI Helm chart."""

        op_cfg = cfg.operator

        # For OCI charts in CDK, repository must be the FULL path including chart name.
        # CDK's Helm Lambda constructs the OCI ref as "{repository}:{version}" without
        # appending the chart name.
        self._operator_chart = eks.HelmChart(
            self, "OpenClawOperator",
            cluster=self.cluster,
            chart="openclaw-operator",
            repository=op_cfg.chart_repository,  # oci://ghcr.io/openclaw-rocks/charts/openclaw-operator
            version=op_cfg.chart_version,
            namespace=op_cfg.namespace,
            create_namespace=True,
            values={
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

        CfnOutput(self, "OperatorChartVersion",
                  value=op_cfg.chart_version,
                  description="OpenClaw Operator Helm chart version")

    # -----------------------------------------------------------
    # 2. Bedrock IAM Role (template)
    # -----------------------------------------------------------
    def _setup_bedrock_role(self, cfg):
        """Create a shared Bedrock IAM Role for agent Pod Identity.

        PoC approach: create one role with Bedrock permissions.
        The Provisioning Service will create Pod Identity Associations
        per-user namespace dynamically at runtime.
        """
        self.bedrock_role = _make_pod_identity_role(
            self, "BedrockAgentRole",
            description="Shared Bedrock access role for OpenClaw agent instances",
        )

        self.bedrock_role.add_to_policy(iam.PolicyStatement(
            sid="BedrockInvoke",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListFoundationModels",
                "bedrock:GetFoundationModel",
            ],
            resources=["*"],
        ))

        CfnOutput(self, "BedrockRoleArn",
                  value=self.bedrock_role.role_arn,
                  description="Bedrock IAM Role ARN for agent Pod Identity")

    # -----------------------------------------------------------
    # 3. Provisioning Namespace + PostgreSQL
    # -----------------------------------------------------------
    def _create_provisioning_namespace(self):
        """Create the openclaw-provisioning namespace."""
        return self._add_manifest("ProvisioningNamespace", {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": PROVISIONING_NS,
            },
        })

    def _setup_postgres(self, ns_manifest):
        """Deploy PostgreSQL StatefulSet with gp2 storage."""

        # Secret
        pg_secret = self._add_manifest("PostgresSecret", {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "postgres-secret",
                "namespace": PROVISIONING_NS,
            },
            "type": "Opaque",
            "stringData": {
                "POSTGRES_PASSWORD": "OpenClaw2026!SecureDB",
                "POSTGRES_USER": "openclaw",
                "POSTGRES_DB": "openclaw",
            },
        })
        pg_secret.node.add_dependency(ns_manifest)

        # Service
        pg_svc = self._add_manifest("PostgresService", {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "postgres",
                "namespace": PROVISIONING_NS,
                "labels": {"app": "postgres"},
            },
            "spec": {
                "type": "ClusterIP",
                "ports": [{
                    "port": 5432,
                    "targetPort": 5432,
                    "protocol": "TCP",
                    "name": "postgres",
                }],
                "selector": {"app": "postgres"},
            },
        })
        pg_svc.node.add_dependency(ns_manifest)

        # StatefulSet
        pg_sts = self._add_manifest("PostgresStatefulSet", {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "name": "postgres",
                "namespace": PROVISIONING_NS,
                "labels": {"app": "postgres"},
            },
            "spec": {
                "serviceName": "postgres",
                "replicas": 1,
                "selector": {"matchLabels": {"app": "postgres"}},
                "template": {
                    "metadata": {"labels": {"app": "postgres"}},
                    "spec": {
                        "tolerations": [{
                            "key": "CriticalAddonsOnly",
                            "operator": "Exists",
                        }],
                        "containers": [{
                            "name": "postgres",
                            "image": "postgres:15-alpine",
                            "ports": [{"containerPort": 5432, "name": "postgres"}],
                            "env": [
                                {
                                    "name": "POSTGRES_USER",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_USER",
                                    }},
                                },
                                {
                                    "name": "POSTGRES_PASSWORD",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_PASSWORD",
                                    }},
                                },
                                {
                                    "name": "POSTGRES_DB",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_DB",
                                    }},
                                },
                                {
                                    "name": "PGDATA",
                                    "value": "/var/lib/postgresql/data/pgdata",
                                },
                            ],
                            "volumeMounts": [{
                                "name": "postgres-data",
                                "mountPath": "/var/lib/postgresql/data",
                            }],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                            "livenessProbe": {
                                "exec": {
                                    "command": ["pg_isready", "-U", "openclaw"],
                                },
                                "initialDelaySeconds": 30,
                                "periodSeconds": 10,
                            },
                            "readinessProbe": {
                                "exec": {
                                    "command": ["pg_isready", "-U", "openclaw"],
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5,
                            },
                        }],
                    },
                },
                "volumeClaimTemplates": [{
                    "metadata": {"name": "postgres-data"},
                    "spec": {
                        "accessModes": ["ReadWriteMany"],
                        "storageClassName": "efs-sc",
                        "resources": {
                            "requests": {"storage": "10Gi"},
                        },
                    },
                }],
            },
        })
        pg_sts.node.add_dependency(pg_secret)
        pg_sts.node.add_dependency(pg_svc)

    # -----------------------------------------------------------
    # 4. Provisioning Service IAM Role
    # -----------------------------------------------------------
    def _setup_provisioning_iam(self, cfg):
        """Create IAM Role for Provisioning Service Pod Identity.

        The Provisioning Service needs AWS credentials to:
        - Create/Delete Pod Identity Associations (eks:*)
        - Pass the shared Bedrock role (iam:PassRole)
        When CREATE_IAM_ROLE_PER_USER=true (future):
        - Create/Delete IAM Roles (iam:CreateRole, etc.)
        """
        self.provisioning_role = _make_pod_identity_role(
            self, "ProvisioningServiceRole",
            description="IAM Role for OpenClaw Provisioning Service (EKS API access)",
        )

        # EKS Pod Identity management
        self.provisioning_role.add_to_policy(iam.PolicyStatement(
            sid="EKSPodIdentityManagement",
            effect=iam.Effect.ALLOW,
            actions=[
                "eks:CreatePodIdentityAssociation",
                "eks:DeletePodIdentityAssociation",
                "eks:ListPodIdentityAssociations",
                "eks:DescribePodIdentityAssociation",
            ],
            resources=["*"],
        ))

        # Allow passing the shared Bedrock role to Pod Identity
        self.provisioning_role.add_to_policy(iam.PolicyStatement(
            sid="PassBedrockRole",
            effect=iam.Effect.ALLOW,
            actions=[
                "iam:PassRole",
                "iam:GetRole",  # Required by eks:CreatePodIdentityAssociation to validate the role
            ],
            resources=[self.bedrock_role.role_arn],
        ))

        # Create Pod Identity Association for the Provisioning Service SA
        eks.CfnPodIdentityAssociation(
            self, "ProvisioningPodIdentity",
            cluster_name=cfg.cluster.name,
            namespace=PROVISIONING_NS,
            service_account=PROVISIONING_SA,
            role_arn=self.provisioning_role.role_arn,
        )

        CfnOutput(self, "ProvisioningRoleArn",
                  value=self.provisioning_role.role_arn,
                  description="Provisioning Service IAM Role ARN")

    # -----------------------------------------------------------
    # 5. Provisioning Service
    # -----------------------------------------------------------
    def _setup_provisioning_service(self, cfg, efs_file_system_id: str, ns_manifest):
        """Deploy Provisioning Service with RBAC, ConfigMap, and Deployment."""

        # ServiceAccount
        sa = self._add_manifest("ProvisionerServiceAccount", {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": PROVISIONING_SA,
                "namespace": PROVISIONING_NS,
            },
        })
        sa.node.add_dependency(ns_manifest)

        # ClusterRole
        cr = self._add_manifest("ProvisionerClusterRole", {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": PROVISIONING_SA},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["namespaces"],
                    "verbs": ["create", "get", "list", "delete"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["resourcequotas", "services"],
                    "verbs": ["create", "get", "list"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["networkpolicies"],
                    "verbs": ["create", "get", "list"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["ingresses"],
                    "verbs": ["create", "get", "list", "update", "patch"],
                },
                {
                    "apiGroups": ["openclaw.rocks"],
                    "resources": ["openclawinstances"],
                    "verbs": ["create", "get", "list", "watch", "delete"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods/exec"],
                    "verbs": ["create", "get"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["endpoints"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["secrets"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["statefulsets"],
                    "verbs": ["get", "list"],
                },
            ],
        })

        # ClusterRoleBinding
        crb = self._add_manifest("ProvisionerClusterRoleBinding", {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": PROVISIONING_SA},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": PROVISIONING_SA,
            },
            "subjects": [{
                "kind": "ServiceAccount",
                "name": PROVISIONING_SA,
                "namespace": PROVISIONING_NS,
            }],
        })
        crb.node.add_dependency(sa)
        crb.node.add_dependency(cr)

        # ConfigMap — uses self.bedrock_role.role_arn (ApplicationStack resource)
        # This is why we use _add_manifest() instead of cluster.add_manifest()
        cm = self._add_manifest("ProvisioningConfigMap", {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "openclaw-provisioning-env",
                "namespace": PROVISIONING_NS,
                "labels": {"app": "openclaw-provisioning"},
            },
            "data": {
                "AWS_REGION": cfg.region,
                "AWS_ACCOUNT_ID": self.account,
                "EKS_CLUSTER_NAME": cfg.cluster.name,
                "EFS_FILE_SYSTEM_ID": efs_file_system_id,
                "SHARED_BEDROCK_ROLE_ARN": self.bedrock_role.role_arn,
                "OPENCLAW_IMG_REPO": "ghcr.io/open-claw",
                "OPENCLAW_STORAGE_CLASS": "efs-sc",
                "OPENCLAW_MODEL": cfg.bedrock.default_model,
                # Public ALB subnets for OpenClaw instance Ingress
                # Required by ALB Controller to resolve subnets for internet-facing ALB
                "PUBLIC_ALB_SUBNETS": cfg.network.public_alb_subnets,
                "USE_PUBLIC_ALB": "true",
            },
        })
        cm.node.add_dependency(ns_manifest)

        # Service
        svc = self._add_manifest("ProvisioningService", {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "openclaw-provisioning",
                "namespace": PROVISIONING_NS,
                "labels": {"app": "openclaw-provisioning"},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": "openclaw-provisioning"},
                "ports": [{
                    "port": 80,
                    "targetPort": 8080,
                    "protocol": "TCP",
                    "name": "http",
                }],
            },
        })
        svc.node.add_dependency(ns_manifest)

        # Deployment
        deploy = self._add_manifest("ProvisioningDeployment", {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "openclaw-provisioning",
                "namespace": PROVISIONING_NS,
                "labels": {"app": "openclaw-provisioning"},
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "openclaw-provisioning"}},
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 1, "maxSurge": 1},
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "openclaw-provisioning",
                            "version": "v1",
                        },
                    },
                    "spec": {
                        "serviceAccountName": PROVISIONING_SA,
                        "nodeSelector": {"role": "system"},
                        "tolerations": [{
                            "key": "CriticalAddonsOnly",
                            "operator": "Exists",
                        }],
                        "securityContext": {
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000,
                            "runAsNonRoot": True,
                        },
                        "containers": [{
                            "name": "provisioning",
                            "image": PROVISIONING_IMAGE,
                            "imagePullPolicy": "Always",
                            "ports": [{
                                "containerPort": 8080,
                                "name": "http",
                                "protocol": "TCP",
                            }],
                            "envFrom": [{
                                "configMapRef": {
                                    "name": "openclaw-provisioning-env",
                                },
                            }],
                            "env": [
                                {"name": "LOG_LEVEL", "value": "INFO"},
                                # PostgreSQL
                                {"name": "POSTGRES_HOST", "value": "postgres"},
                                {"name": "POSTGRES_PORT", "value": "5432"},
                                {
                                    "name": "POSTGRES_DB",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_DB",
                                    }},
                                },
                                {
                                    "name": "POSTGRES_USER",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_USER",
                                    }},
                                },
                                {
                                    "name": "POSTGRES_PASSWORD",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "postgres-secret",
                                        "key": "POSTGRES_PASSWORD",
                                    }},
                                },
                                # Pod Identity
                                {"name": "USE_POD_IDENTITY", "value": "true"},
                                {"name": "CREATE_IAM_ROLE_PER_USER", "value": "false"},
                                # Resource defaults
                                {"name": "OPENCLAW_CPU_REQUEST", "value": "500m"},
                                {"name": "OPENCLAW_MEMORY_REQUEST", "value": "1Gi"},
                                {"name": "OPENCLAW_CPU_LIMIT", "value": "2"},
                                {"name": "OPENCLAW_MEMORY_LIMIT", "value": "4Gi"},
                                {"name": "OPENCLAW_STORAGE_SIZE", "value": "10Gi"},
                            ],
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "512Mi"},
                                "limits": {"cpu": "1000m", "memory": "1Gi"},
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/health", "port": 8080},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 30,
                                "timeoutSeconds": 5,
                                "failureThreshold": 3,
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "/health", "port": 8080},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                                "timeoutSeconds": 3,
                                "failureThreshold": 2,
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }],
                    },
                },
            },
        })
        deploy.node.add_dependency(cm)
        deploy.node.add_dependency(crb)
        deploy.node.add_dependency(svc)

    # -----------------------------------------------------------
    # 5. ALB Ingress
    # -----------------------------------------------------------
    def _setup_alb_ingress(self, ns_manifest):
        """Create internal ALB Ingress for the Provisioning Service.

        ALB is internal (not internet-facing). Public access goes through
        CloudFront → VPC Origin → Internal ALB. This avoids exposing
        port 80 to 0.0.0.0/0 in security groups.
        """

        ingress = self._add_manifest("ProvisioningIngress", {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "openclaw-provisioning",
                "namespace": PROVISIONING_NS,
                "annotations": {
                    "alb.ingress.kubernetes.io/scheme": "internal",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/group.name": ALB_GROUP_NAME,
                    "alb.ingress.kubernetes.io/healthcheck-path": "/health",
                    "alb.ingress.kubernetes.io/healthcheck-protocol": "HTTP",
                    "alb.ingress.kubernetes.io/healthcheck-interval-seconds": "30",
                    "alb.ingress.kubernetes.io/healthy-threshold-count": "2",
                    "alb.ingress.kubernetes.io/unhealthy-threshold-count": "2",
                    "alb.ingress.kubernetes.io/success-codes": "200",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}]',
                    "alb.ingress.kubernetes.io/target-group-attributes":
                        "deregistration_delay.timeout_seconds=60",
                },
                "labels": {"app": "openclaw-provisioning"},
            },
            "spec": {
                "ingressClassName": "alb",
                "rules": [{
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": "openclaw-provisioning",
                                        "port": {"number": 80},
                                    },
                                },
                            },
                        ],
                    },
                }],
            },
        })
        ingress.node.add_dependency(ns_manifest)

        CfnOutput(self, "IngressNote",
                  value="Run: kubectl get ingress -n openclaw-provisioning "
                        "to find the ALB DNS name after deployment",
                  description="How to find the ALB endpoint")
