import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as guardduty from 'aws-cdk-lib/aws-guardduty';
import { NetworkStack } from './network-stack';
import { DatabaseStack } from './database-stack';
import { BackupStack } from './backup-stack';
import { ComputeStack } from './compute-stack';
import { CloudFrontConstruct } from './cloudfront-stack';
import { ObservabilityStack } from './observability-stack';

export class Wiki7CdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const hostedZoneId = ssm.StringParameter.valueForStringParameter(this, '/wiki7/hostedzone/id');
    const hostedZoneName = ssm.StringParameter.valueForStringParameter(this, '/wiki7/hostedzone/name');
    const hostedZone = route53.HostedZone.fromHostedZoneAttributes(this, 'ImportedZone', {
      hostedZoneId,
      zoneName: hostedZoneName,
    });

    const certificateArn = ssm.StringParameter.valueForStringParameter(this, '/wiki7/certificate/arn');
    const certificate = acm.Certificate.fromCertificateArn(this, 'Wiki7Certificate', certificateArn);
    const wafWebAclArn = ssm.StringParameter.valueForStringParameter(this, '/wiki7/waf-webacl/arn');

    const network = new NetworkStack(this, 'Network');

    const database = new DatabaseStack(this, 'Database', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });

    new BackupStack(this, 'Backup', { dbInstance: database.dbInstance });

    const compute = new ComputeStack(this, 'Compute', {
      vpc: network.vpc,
      dbInstance: database.dbInstance,
      dbSecret: database.dbSecret,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
      domainName: 'wiki7.co.il',
    });

    const cloudfront = new CloudFrontConstruct(this, 'CloudFront', {
      originElasticIp: compute.elasticIp,
      hostedZone,
      certificate,
      domainName: 'wiki7.co.il',
      mediawikiStorageBucket: compute.mediawikiStorageBucket,
      wafWebAclArn,
    });

    // Subscriber address for the alarm SNS topic. Set via `-c alarmEmail=…` or the
    // `alarmEmail` key in cdk.json; fail-fast if missing so we don't deploy alarms with no
    // delivery path.
    const alarmEmail = this.node.tryGetContext('alarmEmail');
    if ( !alarmEmail || typeof alarmEmail !== 'string' ) {
      throw new Error('Missing required context: alarmEmail (set in cdk.json or via -c alarmEmail=…)');
    }

    new ObservabilityStack(this, 'Observability', {
      dbInstance: database.dbInstance,
      ec2InstanceId: compute.instance.instanceId,
      distribution: cloudfront.distribution,
      appLogGroup: compute.appLogGroup,
      alarmEmail,
    });

    // GuardDuty — account-level threat detection. ~$3-5/mo at our scale.
    // S3 + Malware + Kubernetes data sources left at defaults (S3 protection on by default
    // since GuardDuty's 2023 update; the rest don't apply to this account).
    new guardduty.CfnDetector(this, 'GuardDutyDetector', {
      enable: true,
      findingPublishingFrequency: 'FIFTEEN_MINUTES',
    });
  }
}
