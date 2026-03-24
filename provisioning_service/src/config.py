"""Application configuration"""
import os
import json

def _parse_json_env(key, default):
    """Parse JSON from environment variable"""
    value = os.environ.get(key)
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default

class Config:
    """Application configuration"""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

    # Session configuration
    SESSION_TYPE = 'sqlalchemy'  # Store sessions in PostgreSQL
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 days in seconds
    SESSION_SQLALCHEMY_TABLE = 'sessions'  # Table name for Flask-Session

    # SQLAlchemy connection string for Flask-Session
    SESSION_SQLALCHEMY = None  # Will be set in main.py after env vars are loaded

    # Cookie settings for CloudFront + ALB setup
    # CRITICAL: Must set SESSION_COOKIE_SECURE=True and SAMESITE='None' for cross-origin requests
    SESSION_COOKIE_SECURE = True  # Required for SameSite=None (HTTPS only)
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to cookie
    SESSION_COOKIE_SAMESITE = 'None'  # Allow cross-site cookie sending (required for CloudFront → ALB)
    SESSION_COOKIE_DOMAIN = None  # Let Flask use the request's host (CloudFront domain)

    # Database
    DATABASE_PATH = os.environ.get('DATABASE_PATH', '/app/data/openclaw.db')

    # Kubernetes
    K8S_NAMESPACE_PREFIX = 'openclaw'

    # OpenClaw Instance 默认配置 (支持环境变量覆盖)
    OPENCLAW_DEFAULTS = {
        'runtime_class': os.environ.get('OPENCLAW_RUNTIME_CLASS') or None,
        'node_selector': _parse_json_env('OPENCLAW_NODE_SELECTOR', {}),
        'tolerations': _parse_json_env('OPENCLAW_TOLERATIONS', []),
        'resources': {
            'requests': {
                'cpu': os.environ.get('OPENCLAW_CPU_REQUEST', '600m'),
                'memory': os.environ.get('OPENCLAW_MEMORY_REQUEST', '1.2Gi')
            },
            'limits': {
                'cpu': os.environ.get('OPENCLAW_CPU_LIMIT', '2'),
                'memory': os.environ.get('OPENCLAW_MEMORY_LIMIT', '4Gi')
            }
        },
        'storage_size': os.environ.get('OPENCLAW_STORAGE_SIZE', '10Gi'),
        'storage_class': os.environ.get('OPENCLAW_STORAGE_CLASS', 'efs-sc'),
        'model': os.environ.get('OPENCLAW_MODEL', 'amazon-bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0'),
        'aws_credentials_secret': os.environ.get('OPENCLAW_AWS_CREDENTIALS_SECRET', 'aws-credentials'),
        'image': {
            'repository': os.environ.get('OPENCLAW_IMAGE_REPOSITORY', 'ghcr.io/openclaw/openclaw'),
            'tag': os.environ.get('OPENCLAW_IMAGE_TAG', 'latest'),
            'pullPolicy': os.environ.get('OPENCLAW_IMAGE_PULL_POLICY', 'IfNotPresent')
        }
    }

    # ResourceQuota 限制
    RESOURCE_QUOTA = {
        'requests.cpu': '2',
        'requests.memory': '4Gi',
        'limits.cpu': '4',
        'limits.memory': '8Gi',
        'persistentvolumeclaims': '2'
    }

    # Available Bedrock models for user selection
    BEDROCK_MODELS = [
        {'id': 'amazon-bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0', 'name': 'Claude Sonnet 4.5', 'provider_label': 'Anthropic', 'default': True},
        {'id': 'amazon-bedrock/us.amazon.nova-pro-v1:0', 'name': 'Amazon Nova Pro', 'provider_label': 'Amazon'},
    ]

    # Available SiliconFlow models for user selection
    SILICONFLOW_MODELS = []

    # SiliconFlow provider configuration (api_key provided by user at provision time)
    SILICONFLOW_DEFAULTS = {
        'base_url': os.environ.get('SILICONFLOW_BASE_URL', 'https://api.siliconflow.cn/v1'),
        'model': os.environ.get('SILICONFLOW_MODEL', 'Pro/deepseek-ai/DeepSeek-V3'),
        'context_window': int(os.environ.get('SILICONFLOW_CONTEXT_WINDOW', '65536')),
        'max_tokens': int(os.environ.get('SILICONFLOW_MAX_TOKENS', '8192')),
    }

    # 超时设置
    K8S_API_TIMEOUT = 30

    # 日志
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

    # AWS 配置
    AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')
    AWS_ACCOUNT_ID = os.environ.get('AWS_ACCOUNT_ID', '')  # AWS Account ID for Cognito ARN

    # EKS 配置
    EKS_CLUSTER_NAME = os.environ.get('EKS_CLUSTER_NAME', 'test-s4')
    USE_POD_IDENTITY = os.environ.get('USE_POD_IDENTITY', 'true').lower() == 'true'

    # Pod Identity 共享 Role 配置
    SHARED_BEDROCK_ROLE_ARN = os.environ.get('SHARED_BEDROCK_ROLE_ARN', '')

    # 是否为每个用户创建独立 IAM Role（设为 False 使用共享 Role）
    CREATE_IAM_ROLE_PER_USER = os.environ.get(
        'CREATE_IAM_ROLE_PER_USER',
        'false'
    ).lower() == 'true'

    # Ingress 配置（Internal ALB + API Gateway）
    INGRESS_ENABLED = os.environ.get('INGRESS_ENABLED', 'true').lower() == 'true'
    INGRESS_CLASS = os.environ.get('INGRESS_CLASS', 'alb')
    INGRESS_GROUP_NAME = os.environ.get('INGRESS_GROUP_NAME', 'openclaw-shared-instances')
    INGRESS_SCHEME = os.environ.get('INGRESS_SCHEME', 'internal')  # Internal ALB（不暴露公网）
    INGRESS_TARGET_TYPE = os.environ.get('INGRESS_TARGET_TYPE', 'ip')  # IP mode for better performance

    # API Gateway 配置（用于构建外部访问 URL）
    API_GATEWAY_ENDPOINT = os.environ.get('API_GATEWAY_ENDPOINT', '')
    API_GATEWAY_STAGE = os.environ.get('API_GATEWAY_STAGE', 'prod')

    # CloudFront + Public ALB 配置（最终生产方案）
    CLOUDFRONT_DOMAIN = os.environ.get('CLOUDFRONT_DOMAIN', '')
    CLOUDFRONT_DISTRIBUTION_ID = os.environ.get('CLOUDFRONT_DISTRIBUTION_ID', '')
    PUBLIC_ALB_DNS = os.environ.get('PUBLIC_ALB_DNS', '')
    PUBLIC_ALB_GROUP_NAME = os.environ.get('PUBLIC_ALB_GROUP_NAME', 'openclaw-shared-instances')

    # Public ALB 子网配置
    PUBLIC_ALB_SUBNETS = os.environ.get('PUBLIC_ALB_SUBNETS', '')

    # Public ALB 安全组（CloudFront SG + ALB managed SG，由部署脚本设置）
    PUBLIC_ALB_SECURITY_GROUPS = os.environ.get('PUBLIC_ALB_SECURITY_GROUPS', '')

    # Gateway 配置（allowedOrigins + trustedProxies）
    GATEWAY_CONFIG = {
        "allowedOrigins": [
            f"https://{os.environ.get('CLOUDFRONT_DOMAIN', '')}",
            f"http://{os.environ.get('PUBLIC_ALB_DNS', '')}",
            f"https://{os.environ.get('PUBLIC_ALB_DNS', '')}"
        ],
        "trustedProxies": [os.environ.get('GATEWAY_TRUSTED_PROXIES', '0.0.0.0/0')]  # 生产环境改为 VPC CIDR 或 CloudFront IP ranges
    }

    # Public ALB Ingress annotations（共享 ALB 模式）
    # Instance Ingress must match provisioning-public Ingress: same group, scheme, subnets, security-groups
    PUBLIC_ALB_INGRESS_ANNOTATIONS = {
        "alb.ingress.kubernetes.io/scheme": "internet-facing",
        "alb.ingress.kubernetes.io/target-type": "ip",
        "alb.ingress.kubernetes.io/group.name": os.environ.get('PUBLIC_ALB_GROUP_NAME', 'openclaw-shared-instances'),
        "alb.ingress.kubernetes.io/subnets": os.environ.get('PUBLIC_ALB_SUBNETS', ''),
        "alb.ingress.kubernetes.io/security-groups": os.environ.get('PUBLIC_ALB_SECURITY_GROUPS', ''),
        "alb.ingress.kubernetes.io/healthcheck-protocol": "HTTP",
        "alb.ingress.kubernetes.io/success-codes": "200,404",
        "alb.ingress.kubernetes.io/target-group-attributes": (
            "stickiness.enabled=true,"
            "stickiness.type=lb_cookie,"
            "stickiness.lb_cookie.duration_seconds=3600,"
            "deregistration_delay.timeout_seconds=60,"
            "load_balancing.algorithm.type=least_outstanding_requests"
        )
    }

    # 使用 Public ALB 模式（覆盖内部 ALB 配置）
    USE_PUBLIC_ALB = os.environ.get('USE_PUBLIC_ALB', 'true').lower() == 'true'

    # Billing sidecar configuration
    BILLING_SIDECAR_ENABLED = os.environ.get('BILLING_SIDECAR_ENABLED', 'true').lower() == 'true'
    BILLING_SIDECAR_IMAGE = os.environ.get('BILLING_SIDECAR_IMAGE', '')  # e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com/billing-sidecar:latest
