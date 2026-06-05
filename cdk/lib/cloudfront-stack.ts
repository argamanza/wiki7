import { Construct } from 'constructs';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as cdk from 'aws-cdk-lib';

interface CloudFrontProps {
  // The EC2 instance's static EIP — CloudFront uses an A-record in the hosted zone as origin.
  originElasticIp: ec2.CfnEIP;
  hostedZone: route53.IHostedZone;
  certificate: acm.ICertificate;
  domainName: string;
  mediawikiStorageBucket: s3.Bucket;
  wafWebAclArn: string;
}

export class CloudFrontConstruct extends Construct {
  constructor(scope: Construct, id: string, props: CloudFrontProps) {
    super(scope, id);

    const { originElasticIp, hostedZone, certificate, domainName, mediawikiStorageBucket, wafWebAclArn } = props;

    // === Stable origin hostname for the EC2 ===================================================
    // CloudFront resolves this via public DNS each time the origin connection is established.
    // If the EIP ever needs to be reallocated, we update this record and CloudFront re-discovers.
    const originRecordName = 'ec2';
    const originDomain = `${originRecordName}.${domainName}`;
    new route53.ARecord(this, 'Wiki7OriginEc2Alias', {
      zone: hostedZone,
      recordName: originRecordName,
      target: route53.RecordTarget.fromIpAddresses(originElasticIp.ref),
      ttl: cdk.Duration.minutes(5),
      comment: 'A-record for the wiki7 EC2 origin used by CloudFront',
    });

    // === Origins ===============================================================================
    // The EC2 instance speaks HTTP on port 80, restricted to CloudFront's prefix list.
    // TODO(phase4): terminate TLS on the instance (Caddy/nginx + ACM-via-S3) and switch to HTTPS_ONLY.
    const ec2Origin = new origins.HttpOrigin(originDomain, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      connectionAttempts: 3,
      connectionTimeout: cdk.Duration.seconds(10),
    });

    const s3Origin = origins.S3BucketOrigin.withOriginAccessControl(mediawikiStorageBucket, {
      originAccessLevels: [cloudfront.AccessLevel.READ],
      connectionTimeout: cdk.Duration.seconds(10),
      connectionAttempts: 3,
      originPath: '/',
      customHeaders: {},
    });

    // === www → apex redirect at the edge ======================================================
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

    // === Response headers — HSTS + frame/XSS protection =======================================
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeadersPolicy', {
      responseHeadersPolicyName: 'Wiki7SecurityHeaders',
      comment: 'Security headers for Wiki7',
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        xssProtection: { protection: true, modeBlock: true, override: true },
        strictTransportSecurity: {
          accessControlMaxAge: cdk.Duration.days(365),
          includeSubdomains: true,
          override: true,
        },
      },
    });

    // === Cache policies =======================================================================
    // S3 uploads under /images and /assets.
    const staticContentCachePolicy = new cloudfront.CachePolicy(this, 'StaticContentCachePolicy', {
      cachePolicyName: 'Wiki7StaticContent',
      comment: 'Cache policy for Wiki7 static content served from S3',
      defaultTtl: cdk.Duration.days(7),
      minTtl: cdk.Duration.days(1),
      maxTtl: cdk.Duration.days(30),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
      headerBehavior: cloudfront.CacheHeaderBehavior.allowList(
        'Origin', 'Access-Control-Request-Method', 'Access-Control-Request-Headers',
      ),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.none(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
    });

    // MediaWiki ResourceLoader + skin/extension static files served from EC2.
    // MW versions every load.php URL via query string, so forwarding all QS as the cache key is correct.
    const mediawikiAssetsCachePolicy = new cloudfront.CachePolicy(this, 'MediawikiAssetsCachePolicy', {
      cachePolicyName: 'Wiki7MediawikiAssets',
      comment: 'Cache policy for /load.php, /skins/*, /extensions/* served from the EC2 origin',
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
      origin: ec2Origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      cachePolicy: mediawikiAssetsCachePolicy,
      responseHeadersPolicy: responseHeadersPolicy,
      compress: true,
    };

    // === The distribution =====================================================================
    const distribution = new cloudfront.Distribution(this, 'Wiki7Distribution', {
      defaultBehavior: {
        origin: ec2Origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        // Dynamic pages — uncached. MW emits its own cache headers for browser caching.
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
        responseHeadersPolicy: responseHeadersPolicy,
        functionAssociations: [
          { function: redirectFunction, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
        ],
      },
      additionalBehaviors: {
        // Uploads — served by CloudFront → S3 directly via OAC, bypassing the instance entirely.
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

    // === DNS — IPv4 + IPv6 alias for apex + www ===============================================
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

    new cdk.CfnOutput(this, 'DistributionId', {
      value: distribution.distributionId,
      description: 'CloudFront Distribution ID',
    });
    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: distribution.distributionDomainName,
      description: 'CloudFront Distribution Domain Name',
    });
    new cdk.CfnOutput(this, 'Ec2OriginDomain', {
      value: originDomain,
      description: 'EC2 origin DNS name used by CloudFront',
    });
  }
}
