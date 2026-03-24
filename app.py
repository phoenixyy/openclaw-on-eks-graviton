#!/usr/bin/env python3
"""
OpenClaw on EKS Graviton — CDK Application Entry Point

Deploys a multi-tenant AI Agent platform on Amazon EKS with Graviton (ARM64).

Usage:
    cdk deploy FoundationStack      # VPC + EKS + EFS + Karpenter (~25 min)
    cdk deploy ApplicationStack     # Operator + Provisioning Service
    cdk deploy --all                # Everything
    cdk destroy --all               # Clean up
"""

import os
import aws_cdk as cdk
from cdk_stacks.config import config
from cdk_stacks.foundation_stack import FoundationStack
from cdk_stacks.application_stack import ApplicationStack
from cdk_stacks.cdn_stack import CdnStack


app = cdk.App()

# [FIX I2] Use CDK_DEFAULT_ACCOUNT/REGION for proper AZ resolution
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=config.region,
)

# Stack 1: Foundation — VPC, EKS, EFS, Karpenter, ALB Controller
foundation = FoundationStack(
    app, "FoundationStack",
    env=env,
    description="OpenClaw multi-tenant platform: VPC + EKS Graviton + EFS + Karpenter",
)

# Stack 2: Application — Operator, Provisioning Service, PostgreSQL, ALB Ingress
application = ApplicationStack(
    app, "ApplicationStack",
    env=env,
    cluster=foundation.cluster,
    vpc=foundation.vpc,
    efs_file_system_id=foundation.file_system.file_system_id,
    description="OpenClaw application layer: Operator + Provisioning Service + PostgreSQL",
)
application.add_dependency(foundation)

# Stack 3: CDN — CloudFront with VPC Origin → Internal ALB
# ALB is created by K8s ALB Controller after ApplicationStack deploys.
# Pass ALB ARN via context: cdk deploy CdnStack -c alb_arn=<ARN> -c alb_sg_id=<SG_ID>
# Or set in cdk.context.json after first deployment.
alb_arn = app.node.try_get_context("alb_arn")
if alb_arn:
    cdn = CdnStack(
        app, "CdnStack",
        env=cdk.Environment(
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            region=config.region,
        ),
        vpc=foundation.vpc,
        description="OpenClaw CDN: CloudFront VPC Origin → Internal ALB",
    )
    cdn.add_dependency(application)

# Apply global tags
for key, value in config.tags.items():
    cdk.Tags.of(app).add(key, value)

app.synth()
