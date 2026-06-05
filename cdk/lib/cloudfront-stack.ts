import { Construct } from 'constructs';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cdk from 'aws-cdk-lib';

interface CloudFrontProps {
  alb: elbv2.ApplicationLoadBalancer;
  hostedZone: route53.IHostedZone;
  certificate: acm.ICertificate;
  domainName: string;
  mediawikiStorageBucket: s3.Bucket;
  wafWebAclArn: string;
}

export class CloudFrontConstruct extends Construct {
  constructor(scope: Construct, id: string, props: CloudFrontProps) {
    super(scope, id);

    const { alb, hostedZone, certificate, domainName, mediawikiStorageBucket, wafWebAclArn } = props;

    // ALB Origin.
    // TODO(phase4): switch to HTTPS_ONLY once the ALB has a regional ACM cert + 443 listener.
    const albOrigin = new origins.LoadBalancerV2Origin(alb, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
    });

    // S3 Origin with OAC - using correct props
    const s3Origin = origins.S3BucketOrigin.withOriginAccessControl(mediawikiStorageBucket, {
      originAccessLevels: [cloudfront.AccessLevel.READ],
      connectionTimeout: cdk.Duration.seconds(10),
      connectionAttempts: 3,
      originPath: '/',  // Default, but explicit for clarity
      customHeaders: {}, // No custom headers needed
    });

    // Redirect Function
    const redirectFunction = new cloudfront.Function(this, 'RedirectWwwToApexFunction', {
      code: cloudfront.FunctionCode.fromInline(`
        function handler(event) {
          var request = event.request;
          var host = request.headers.host.value;
          if (host.startsWith('www.')) {
            var redirect = 'https://' + host.substring(4) + request.uri;
            return {
              statusCode: 301,
              statusDescription: 'Moved Permanently',
              headers: {
                location: { value: redirect }
              }
            };
          }
          return request;
        }
      `),
    });

    // Security Headers Policy
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeadersPolicy', {
      responseHeadersPolicyName: 'Wiki7SecurityHeaders',
      comment: 'Security headers for Wiki7',
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: {
          frameOption: cloudfront.HeadersFrameOption.DENY,
          override: true,
        },
        xssProtection: {
          protection: true,
          modeBlock: true,
          override: true,
        },
        strictTransportSecurity: {
          accessControlMaxAge: cdk.Duration.days(365),
          includeSubdomains: true,
          override: true,
        },
      },
    });

    // Cache policy for S3-served static content (uploaded images via CloudFront → S3 OAC).
    const staticContentCachePolicy = new cloudfront.CachePolicy(this, 'StaticContentCachePolicy', {
      cachePolicyName: 'Wiki7StaticContent',
      comment: 'Cache policy for Wiki7 static content',
      defaultTtl: cdk.Duration.days(7),
      minTtl: cdk.Duration.days(1),
      maxTtl: cdk.Duration.days(30),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
      headerBehavior: cloudfront.CacheHeaderBehavior.allowList('Origin', 'Access-Control-Request-Method', 'Access-Control-Request-Headers'),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.none(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
    });

    // Cache policy for MediaWiki ResourceLoader + static skin/extension assets served by the ALB.
    // MediaWiki versions every load.php URL via query string, so forwarding all QS as the cache key is correct.
    const mediawikiAssetsCachePolicy = new cloudfront.CachePolicy(this, 'MediawikiAssetsCachePolicy', {
      cachePolicyName: 'Wiki7MediawikiAssets',
      comment: 'Cache policy for /load.php, /skins/*, /extensions/* served from the ALB',
      defaultTtl: cdk.Duration.days(1),
      minTtl: cdk.Duration.hours(1),
      maxTtl: cdk.Duration.days(30),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
      headerBehavior: cloudfront.CacheHeaderBehavior.none(),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.all(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
    });

    const mediawikiAssetsBehavior: cloudfront.BehaviorOptions = {
      origin: albOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      cachePolicy: mediawikiAssetsCachePolicy,
      responseHeadersPolicy: responseHeadersPolicy,
      compress: true,
    };

    // Create CloudFront distribution
    const distribution = new cloudfront.Distribution(this, 'Wiki7Distribution', {
      defaultBehavior: {
        origin: albOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
        responseHeadersPolicy: responseHeadersPolicy,
        functionAssociations: [
          {
            function: redirectFunction,
            eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
          },
        ],
      },
      additionalBehaviors: {
        'images/*': {
          origin: s3Origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: staticContentCachePolicy,
          responseHeadersPolicy: responseHeadersPolicy,
          compress: true,
        },
        'assets/*': {
          origin: s3Origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: staticContentCachePolicy,
          responseHeadersPolicy: responseHeadersPolicy,
          compress: true,
        },
        // MediaWiki ResourceLoader + skin/extension static files — biggest CDN win per request.
        'load.php': mediawikiAssetsBehavior,
        'skins/*': mediawikiAssetsBehavior,
        'extensions/*': mediawikiAssetsBehavior,
      },
      domainNames: [domainName, `www.${domainName}`],
      certificate,
      webAclId: wafWebAclArn,
    });

    // DNS records — IPv4 + IPv6 alias for both apex and www.
    new route53.ARecord(this, 'Wiki7ApexAlias', {
      zone: hostedZone,
      recordName: '',
      target: route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution)),
    });

    new route53.AaaaRecord(this, 'Wiki7ApexAliasV6', {
      zone: hostedZone,
      recordName: '',
      target: route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution)),
    });

    new route53.ARecord(this, 'Wiki7WwwAlias', {
      zone: hostedZone,
      recordName: 'www',
      target: route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution)),
    });

    new route53.AaaaRecord(this, 'Wiki7WwwAliasV6', {
      zone: hostedZone,
      recordName: 'www',
      target: route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution)),
    });
    
    // Output the distribution domain name and ID
    new cdk.CfnOutput(this, 'DistributionId', {
      value: distribution.distributionId,
      description: 'CloudFront Distribution ID',
    });
    
    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: distribution.distributionDomainName,
      description: 'CloudFront Distribution Domain Name',
    });
  }
}