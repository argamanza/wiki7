import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as ssm from 'aws-cdk-lib/aws-ssm';

interface Wiki7DnsStackProps extends cdk.StackProps {
  domainName: string;
}

export class Wiki7DnsStack extends cdk.Stack {
  readonly hostedZone: route53.HostedZone;

  constructor(scope: Construct, id: string, props: Wiki7DnsStackProps) {
    super(scope, id, props);

    this.hostedZone = new route53.HostedZone(this, 'Wiki7HostedZone', {
      zoneName: props.domainName,
      comment: 'Hosted zone for Wiki7.co.il',
    });

    new ssm.StringParameter(this, 'Wiki7HostedZoneIdParameter', {
      parameterName: '/wiki7/hostedzone/id',
      stringValue: this.hostedZone.hostedZoneId,
    });

    new ssm.StringParameter(this, 'Wiki7HostedZoneNameParameter', {
      parameterName: '/wiki7/hostedzone/name',
      stringValue: this.hostedZone.zoneName,
    });

    // === Search Console verification TXT record ==============================================
    // Google Search Console prefers DNS-based verification — the record survives MediaWiki
    // redeploys and CloudFront reconfigurations, unlike an HTML meta tag.
    //
    // The verification value is read from CDK context (no value → no TXT record). Bootstrap:
    //   1. https://search.google.com/search-console → add property wiki7.co.il (Domain).
    //   2. Copy the token (looks like "google-site-verification=abc...xyz").
    //   3. Set the value persistently in cdk/cdk.json under "context", e.g.
    //        "googleSiteVerification": "google-site-verification=abc...xyz"
    //      (committing it is fine — TXT records are public info.)
    //   4. `npx cdk deploy Wiki7DnsStack`
    //   5. Back in Search Console, click "Verify".
    const googleVerification = this.node.tryGetContext('googleSiteVerification') as string | undefined;
    if (googleVerification) {
      new route53.TxtRecord(this, 'GoogleSiteVerificationTxt', {
        zone: this.hostedZone,
        values: [googleVerification],
        ttl: cdk.Duration.hours(1),
        comment: 'Google Search Console domain verification (set via CDK context googleSiteVerification)',
      });
    }

    new cdk.CfnOutput(this, 'NameServers', {
      value: cdk.Fn.join(', ', this.hostedZone.hostedZoneNameServers!),
      description: 'NS records to copy to domain registrar',
    });
  }
}
