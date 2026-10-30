"""Static site on S3, served by CloudFront, which also routes /api/* to the backend (one origin, no CORS)."""

from pathlib import Path
from typing import Any

from aws_cdk import Annotations, CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from agentops_infra.backend_stack import BackendStack

# Next's static export writes /incidents/INC-1001/index.html; S3 has no notion of a directory index.
_REWRITE = """\
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else if (uri.indexOf('.') === -1) {
    request.uri = uri + '/index.html';
  }
  return request;
}
"""

# Paths CloudFront hands to the API instead of S3.
API_PATHS = ("/api/*", "/health")


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        backend: BackendStack,
        site_dir: Path,
        placeholder_dir: Path,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.bucket = s3.Bucket(
            self,
            "Site",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # build output: always reproducible from git
            auto_delete_objects=True,
        )

        rewrite = cloudfront.Function(
            self,
            "DirectoryIndex",
            code=cloudfront.FunctionCode.from_inline(_REWRITE),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )
        security_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override=True
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365), include_subdomains=True, override=True
                ),
            ),
        )

        api_origin = origins.HttpOrigin(
            f"{backend.api.http_api_id}.execute-api.{self.region}.amazonaws.com",
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        )
        api_behavior = cloudfront.BehaviorOptions(
            origin=api_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,  # responses change per investigation
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            response_headers_policy=security_headers,
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Cdn",
            comment="AgentOps static site and API",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US/Canada/Europe edges: cheapest
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=security_headers,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=rewrite, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST
                    )
                ],
            ),
            additional_behaviors={path: api_behavior for path in API_PATHS},
            error_responses=[
                # With OAC and no s3:ListBucket, S3 answers 403 for a key that does not exist.
                cloudfront.ErrorResponse(http_status=403, response_http_status=404, response_page_path="/404.html"),
                cloudfront.ErrorResponse(http_status=404, response_http_status=404, response_page_path="/404.html"),
            ],
        )

        if not site_dir.is_dir():
            Annotations.of(self).add_warning_v2(
                "agentops:missing-site-build",
                f"{site_dir} is missing: deploying a placeholder page. Run `npm run build` in frontend/ first.",
            )
        s3deploy.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3deploy.Source.asset(str(site_dir if site_dir.is_dir() else placeholder_dir))],
            destination_bucket=self.bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
            prune=True,
            cache_control=[s3deploy.CacheControl.from_string("public, max-age=0, must-revalidate")],
            memory_limit=512,
        )

        Tags.of(self).add("Component", "frontend")
        CfnOutput(self, "SiteUrl", value=f"https://{self.distribution.distribution_domain_name}")
        CfnOutput(self, "DistributionId", value=self.distribution.distribution_id)
