"""OpenClawInstance CRD operations"""
from app.config import Config
from datetime import datetime
import logging
import copy
import os

logger = logging.getLogger(__name__)

def create_openclaw_instance(k8s_client, instance_id, namespace, user_email, cognito_sub=None, custom_config=None, role_arn=None, provider='bedrock', siliconflow_api_key=None, model=None, user_id=None, display_name=None):
    """
    Create an OpenClawInstance CRD

    Args:
        k8s_client: K8sClient instance
        instance_id: Instance ID for naming (e.g. '08c2423c' or '08c2423c-02')
        namespace: Namespace name
        user_email: User email address
        cognito_sub: Cognito Sub ID (optional)
        custom_config: Custom configuration to override defaults (optional)
        role_arn: IAM Role ARN for Pod Identity (optional)
        provider: LLM provider - 'bedrock' or 'siliconflow' (default: 'bedrock')
        siliconflow_api_key: SiliconFlow API key (required when provider='siliconflow')
        model: Model ID override (optional, works for both bedrock and siliconflow)
        user_id: User ID for labels (defaults to instance_id for backward compat)
        display_name: Display name for the instance (optional)

    Returns:
        Tuple of (instance, created)
    """
    if user_id is None:
        user_id = instance_id
    instance_name = f"openclaw-{instance_id}"

    # Merge configuration (custom_config overrides defaults)
    config = copy.deepcopy(Config.OPENCLAW_DEFAULTS)
    logger.info(f"🔍 DEFAULTS runtime_class={config.get('runtime_class')}, node_selector={config.get('node_selector')}")
    logger.info(f"🔍 custom_config received: {custom_config}")
    if custom_config:
        _deep_merge(config, custom_config)
    logger.info(f"🔍 MERGED runtime_class={config.get('runtime_class')}, node_selector={config.get('node_selector')}")

    # Build config.raw based on provider
    if provider == 'siliconflow':
        sf = Config.SILICONFLOW_DEFAULTS
        sf_model_id = model if model else sf['model']
        config_raw = {
            "gateway": {
                "controlUi": {
                    "basePath": f"/instance/{instance_id}",
                    "allowedOrigins": Config.GATEWAY_CONFIG["allowedOrigins"],
                    "dangerouslyDisableDeviceAuth": True
                },
                "trustedProxies": Config.GATEWAY_CONFIG["trustedProxies"]
            },
            "models": {
                "providers": {
                    "siliconflow": {
                        "baseUrl": sf['base_url'],
                        "api": "openai-completions",
                        "auth": "api-key",
                        "apiKey": siliconflow_api_key,
                        "models": [{
                            "id": sf_model_id,
                            "name": "SiliconFlow Model",
                            "input": ["text"],
                            "contextWindow": sf['context_window'],
                            "maxTokens": sf['max_tokens']
                        }]
                    }
                }
            },
            "agents": {
                "defaults": {
                    "model": {
                        "primary": f"siliconflow/{sf_model_id}"
                    }
                }
            }
        }
    else:  # bedrock (default)
        bedrock_model = model if model else config['model']
        config_raw = {
            "gateway": {
                "controlUi": {
                    "basePath": f"/instance/{instance_id}",
                    "allowedOrigins": Config.GATEWAY_CONFIG["allowedOrigins"],
                    "dangerouslyDisableDeviceAuth": True
                },
                "trustedProxies": Config.GATEWAY_CONFIG["trustedProxies"]
            },
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": f"https://bedrock-runtime.{Config.AWS_REGION}.amazonaws.com",
                        "api": "bedrock-converse-stream",
                        "auth": "aws-sdk",
                        "models": [
                            {
                                "id": "jp.anthropic.claude-sonnet-4-5-20250929-v1:0",
                                "name": "Claude Sonnet 4.5",
                                "reasoning": True,
                                "input": ["text", "image"],
                                "contextWindow": 200000,
                                "maxTokens": 16384
                            },
                            {
                                "id": "global.anthropic.claude-opus-4-6-v1",
                                "name": "Claude Opus 4.6",
                                "reasoning": True,
                                "input": ["text", "image"],
                                "contextWindow": 200000,
                                "maxTokens": 32000
                            },
                            {
                                "id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
                                "name": "Claude Haiku 4.5",
                                "input": ["text", "image"],
                                "contextWindow": 200000,
                                "maxTokens": 8192
                            },
                            {
                                "id": "jp.amazon.nova-2-lite-v1:0",
                                "name": "Amazon Nova 2 Lite",
                                "input": ["text", "image"],
                                "contextWindow": 300000,
                                "maxTokens": 5120
                            }
                        ]
                    }
                },
                "bedrockDiscovery": {
                    "enabled": True,
                    "region": Config.AWS_REGION
                }
            },
            "agents": {
                "defaults": {
                    "model": {
                        "primary": bedrock_model
                    },
                    # Disable thinking/reasoning for models that don't support it (e.g. Nova Pro)
                    **({"thinkingDefault": "off"} if "nova" in bedrock_model.lower() else {})
                }
            }
        }

    # Build labels
    labels = {
        "openclaw.rocks/user-id": user_id,
        "app.kubernetes.io/managed-by": "openclaw-provisioning-service",
        "openclaw.rocks/llm-provider": provider
    }
    if display_name:
        # Kubernetes labels: max 63 chars, only alphanumeric, '-', '_', '.'
        # Sanitize: replace invalid chars (like ':') with '-'
        import re as _re
        sanitized = _re.sub(r'[^A-Za-z0-9._-]', '-', display_name)[:63]
        # Must start and end with alphanumeric
        sanitized = sanitized.strip('-_.')
        if sanitized:
            labels["openclaw.rocks/display-name"] = sanitized

    # Build RBAC section
    rbac_config = {
        "createServiceAccount": True,
    }
    # Note: Do NOT add eks.amazonaws.com/role-arn annotation when using Pod Identity
    # Pod Identity Association handles the ServiceAccount → IAM Role mapping
    # Adding IRSA annotation causes "AssumeRoleWithWebIdentity" error

    # Build OpenClawInstance CRD
    instance_body = {
        "apiVersion": "openclaw.rocks/v1alpha1",
        "kind": "OpenClawInstance",
        "metadata": {
            "name": instance_name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {
                "openclaw.rocks/created-at": datetime.utcnow().isoformat(),
                "openclaw.rocks/provisioning-method": "eks-pod-service",
                "openclaw.rocks/user-email": user_email
            }
        },
        "spec": {
            "image": config['image'],
            "config": {
                "raw": config_raw
            },
            "resources": config['resources'],
            "availability": {
                "runtimeClassName": config['runtime_class'],
                "nodeSelector": config['node_selector'],
                "tolerations": config['tolerations']
            },
            "storage": {
                "persistence": {
                    "enabled": True,
                    "size": config['storage_size'],
                    "storageClass": config['storage_class'],
                    "accessModes": ["ReadWriteOnce"] if config['storage_class'] == 'gp3' else ["ReadWriteMany"]
                }
            },
            "networking": {
                "service": {
                    "type": "ClusterIP"
                },
                "ingress": _build_ingress_config(instance_id) if Config.INGRESS_ENABLED else {"enabled": False}
            },
            "security": {
                "podSecurityContext": {
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                    "fsGroup": 1000,
                    "runAsNonRoot": True
                },
                "containerSecurityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": False,
                    "capabilities": {"drop": ["ALL"]}
                },
                "networkPolicy": {
                    "enabled": False,
                    "allowDNS": True
                },
                "rbac": rbac_config
            },
            "observability": {
                "metrics": {
                    "enabled": True,
                    "port": 9090
                },
                "logging": {
                    "level": "info",
                    "format": "json"
                }
            }
        }
    }

    # Add AWS_REGION environment variable for Bedrock (required for Pod Identity)
    if provider == 'bedrock':
        instance_body["spec"]["env"] = [
            {
                "name": "AWS_REGION",
                "value": Config.AWS_REGION
            },
            {
                "name": "AWS_DEFAULT_REGION",
                "value": Config.AWS_REGION
            }
        ]
        # Enable selfConfigure to allow Pod Identity (automountServiceAccountToken=true)
        instance_body["spec"]["selfConfigure"] = {
            "enabled": True
        }
        logger.info(f"✅ Added AWS_REGION={Config.AWS_REGION} and enabled selfConfigure for Bedrock Pod Identity")

    # Add billing sidecar if enabled and image is configured
    if Config.BILLING_SIDECAR_ENABLED and Config.BILLING_SIDECAR_IMAGE:
        db_host = 'postgres.openclaw-provisioning.svc'
        db_port = os.environ.get('POSTGRES_PORT', '5432')
        db_name = os.environ.get('POSTGRES_DB', 'openclaw')
        db_user = os.environ.get('POSTGRES_USER', 'openclaw')
        db_pass = os.environ.get('POSTGRES_PASSWORD', 'OpenClaw2026!SecureDB')
        database_url = f"postgres://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

        billing_sidecar = {
            "name": "billing-sidecar",
            "image": Config.BILLING_SIDECAR_IMAGE,
            "env": [
                {"name": "TENANT_ID", "value": user_id},
                {"name": "DATABASE_URL", "value": database_url},
                {"name": "OPENCLAW_SESSIONS_DIR", "value": "/home/openclaw/.openclaw/agents"},
                {"name": "POLL_INTERVAL", "value": "5"},
            ],
            "volumeMounts": [
                {
                    "name": "data",
                    "mountPath": "/home/openclaw/.openclaw",
                    "readOnly": True,
                }
            ],
            "resources": {
                "requests": {"cpu": "10m", "memory": "32Mi"},
                "limits": {"cpu": "50m", "memory": "64Mi"},
            },
        }
        instance_body["spec"].setdefault("sidecars", []).append(billing_sidecar)
        logger.info(f"✅ Billing sidecar injected for user {user_id}")

    # Add cognito_sub if provided
    if cognito_sub:
        instance_body["metadata"]["labels"]["openclaw.rocks/cognito-sub"] = cognito_sub

    def create():
        return k8s_client.custom_objects.create_namespaced_custom_object(
            group="openclaw.rocks",
            version="v1alpha1",
            namespace=namespace,
            plural="openclawinstances",
            body=instance_body
        )

    def get():
        return k8s_client.custom_objects.get_namespaced_custom_object(
            group="openclaw.rocks",
            version="v1alpha1",
            namespace=namespace,
            plural="openclawinstances",
            name=instance_name
        )

    return k8s_client.create_or_get(create, get, f"OpenClawInstance {instance_name}")


def _build_ingress_config(user_id):
    """
    Build Ingress configuration for Public ALB behind CloudFront (NEW) or Internal ALB via API Gateway (OLD)

    Architecture (Public ALB + CloudFront - RECOMMENDED):
      User → CloudFront (HTTPS) → Internet-Facing ALB → OpenClaw

    Architecture (Internal ALB + API Gateway - OLD):
      User → API Gateway (JWT auth) → VPC Link → Internal ALB → OpenClaw

    Args:
        user_id: User ID for generating unique path

    Returns:
        Dict: Ingress configuration
    """
    if Config.USE_PUBLIC_ALB:
        # Public ALB + CloudFront 模式
        config = {
            "enabled": True,
            "className": Config.INGRESS_CLASS,
            "annotations": {
                # Merge Public ALB annotations
                **Config.PUBLIC_ALB_INGRESS_ANNOTATIONS,
                # Override healthcheck path
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/healthcheck-path": f"/instance/{user_id}/",
            },
            # Host-based routing with CloudFront domain
            "hosts": [{
                "host": Config.CLOUDFRONT_DOMAIN,
                "paths": [{
                    "path": f"/instance/{user_id}",
                    "pathType": "Prefix"
                }]
            }]
        }

        logger.info(f"✅ Public ALB Ingress configured for user {user_id} - path: /instance/{user_id}")
        logger.info(f"   Access via CloudFront: https://{Config.CLOUDFRONT_DOMAIN}/instance/{user_id}/")
        logger.info(f"   Direct ALB access: http://{Config.PUBLIC_ALB_DNS}/instance/{user_id}/")

    else:
        # Internal ALB + API Gateway 模式（原有逻辑）
        config = {
            "enabled": True,
            "className": Config.INGRESS_CLASS,
            "annotations": {
                # ALB Ingress Group - share single internal ALB across all instances
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/group.name": Config.INGRESS_GROUP_NAME,

                # Internal ALB - not exposed to internet
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/scheme": Config.INGRESS_SCHEME,

                # IP target mode for better performance
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/target-type": Config.INGRESS_TARGET_TYPE,

                # Health check
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/healthcheck-path": f"/instance/{user_id}/",
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/healthcheck-protocol": "HTTP",
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/success-codes": "200,404",  # 404 ok if gateway requires auth

                # Target Group Attributes - WebSocket optimization
                f"{Config.INGRESS_CLASS}.ingress.kubernetes.io/target-group-attributes": (
                    "stickiness.enabled=true,"
                    "stickiness.type=lb_cookie,"
                    "stickiness.lb_cookie.duration_seconds=3600,"
                    "deregistration_delay.timeout_seconds=60,"
                    "load_balancing.algorithm.type=least_outstanding_requests"
                ),
            },
            # Path-based routing only (no host - accessed via API Gateway)
            "hosts": [{
                "host": "",  # Empty host for path-based routing
                "paths": [{
                    "path": f"/instance/{user_id}",
                    "pathType": "Prefix"
                }]
            }]
        }

        logger.info(f"✅ Internal ALB Ingress configured for user {user_id} - path: /instance/{user_id}")
        logger.info(f"   Access via API Gateway: {Config.API_GATEWAY_ENDPOINT}/{Config.API_GATEWAY_STAGE}/instance/{user_id}/")

    return config


def _deep_merge(base, override):
    """
    Deep merge two dictionaries

    Args:
        base: Base dictionary (will be modified)
        override: Override dictionary
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
