#!/usr/bin/env python3
"""
OpenClaw on EKS Graviton — CDK Application Entry Point

Deploys a multi-tenant AI Agent platform on Amazon EKS with Graviton (ARM64).

Usage:
    cdk deploy FoundationStack      # VPC + EKS + EFS + Karpenter (~25 min)
    cdk deploy --all                # Everything
    cdk destroy --all               # Clean up
"""

import os
import aws_cdk as cdk
from cdk_stacks.config import config
from cdk_stacks.foundation_stack import FoundationStack


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

# Apply global tags
for key, value in config.tags.items():
    cdk.Tags.of(app).add(key, value)

app.synth()
