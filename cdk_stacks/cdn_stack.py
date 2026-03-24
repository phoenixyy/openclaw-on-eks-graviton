"""
CDN Stack: CloudFront distribution with VPC Origin → Internal ALB

Provides HTTPS via *.cloudfront.net domain (no custom domain needed).

Architecture:
    User → CloudFront (HTTPS) → VPC Origin → Internal ALB (HTTP) → Pod

The ALB is internal (not internet-facing), so no 0.0.0.0/0 SG rules needed.
CloudFront reaches the ALB through a VPC Origin ENI inside the VPC.

Deployment (two-phase):
  Phase 1: cdk deploy FoundationStack ApplicationStack
  Phase 2: Get ALB info from K8s, then:
    kubectl get ingress -n openclaw-provisioning openclaw-provisioning \
      -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/load-balancer-arn}'
    cdk deploy CdnStack \
      -c alb_arn=<ALB_ARN> \
      -c alb_dns=<INTERNAL_ALB_DNS> \
      -c alb_sg_id=<ALB_SG_ID>
"""

from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_elasticloadbalancingv2 as elbv2,
    aws_ec2 as ec2,
)

from .config import config


class CdnStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------------------------------
        # Read context parameters (set after ApplicationStack deploys)
        # -------------------------------------------------------
        alb_arn = self.node.try_get_context("alb_arn")
        alb_sg_id = self.node.try_get_context("alb_sg_id") or "sg-placeholder"
        alb_dns = self.node.try_get_context("alb_dns") or ""
        instances_alb_dns = self.node.try_get_context("instances_alb_dns") or ""

        if not alb_arn or not alb_dns:
            raise ValueError(
                "CdnStack requires ALB ARN and DNS. Deploy ApplicationStack first, then:\n"
                "  cdk deploy CdnStack -c alb_arn=<ARN> -c alb_dns=<DNS> -c alb_sg_id=<SG_ID>"
            )

        # -------------------------------------------------------
        # Import the internal ALB created by K8s ALB Controller
        # -------------------------------------------------------
        alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
            self, "ImportedAlb",
            load_balancer_arn=alb_arn,
            security_group_id=alb_sg_id,
            load_balancer_dns_name=alb_dns,
        )

        # -------------------------------------------------------
        # VPC Origin: CloudFront → Internal ALB (no public exposure)
        # with_application_load_balancer creates the VpcOrigin resource
        # and returns an IOrigin for use in Distribution behaviors.
        # -------------------------------------------------------
        provisioning_origin = origins.VpcOrigin.with_application_load_balancer(
            alb,
            http_port=80,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            keepalive_timeout=Duration.seconds(60),
            read_timeout=Duration.seconds(60),
            vpc_origin_name=f"{config.project_name}-provisioning-alb",
        )

        # -------------------------------------------------------
        # Cache Policy: No caching (all dynamic content)
        # -------------------------------------------------------
        cache_policy = cloudfront.CachePolicy.CACHING_DISABLED
        origin_request_policy = cloudfront.OriginRequestPolicy.ALL_VIEWER_AND_CLOUDFRONT_2022

        # -------------------------------------------------------
        # Additional behaviors for /instance/* (optional)
        # -------------------------------------------------------
        additional_behaviors = {}
        if instances_alb_dns:
            instances_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
                self, "ImportedInstancesAlb",
                load_balancer_arn="",  # placeholder, ARN not needed for DNS-only routing
                security_group_id="sg-placeholder",
                load_balancer_dns_name=instances_alb_dns,
            )
            instances_origin = origins.VpcOrigin.with_application_load_balancer(
                instances_alb,
                http_port=80,
                protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
                keepalive_timeout=Duration.seconds(60),
                read_timeout=Duration.seconds(60),
                vpc_origin_name=f"{config.project_name}-instances-alb",
            )
            additional_behaviors["/instance/*"] = cloudfront.BehaviorOptions(
                origin=instances_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cache_policy,
                origin_request_policy=origin_request_policy,
                compress=True,
            )

        # -------------------------------------------------------
        # CloudFront Distribution
        # -------------------------------------------------------
        self.distribution = cloudfront.Distribution(
            self, "Distribution",
            comment=f"{config.project_name} - Provisioning Service HTTPS endpoint",
            default_behavior=cloudfront.BehaviorOptions(
                origin=provisioning_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cache_policy,
                origin_request_policy=origin_request_policy,
                compress=True,
            ),
            additional_behaviors=additional_behaviors,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            enable_ipv6=True,
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
        )

        # -------------------------------------------------------
        # Outputs
        # -------------------------------------------------------
        self.domain_name = self.distribution.distribution_domain_name

        CfnOutput(self, "CloudFrontDomain",
                  value=f"https://{self.domain_name}",
                  description="CloudFront HTTPS endpoint for the Provisioning Service")

        CfnOutput(self, "CloudFrontDistributionId",
                  value=self.distribution.distribution_id,
                  description="CloudFront Distribution ID")
