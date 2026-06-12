import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';

export class NetworkStack extends Construct {
  readonly vpc: ec2.Vpc;
  readonly mediawikiSecurityGroup: ec2.SecurityGroup;
  readonly databaseSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    // Create the VPC — no NAT Gateway (saves ~$35/mo)
    // The EC2 instance and RDS run in public subnets; RDS publiclyAccessible=false + SG restricts access
    this.vpc = new ec2.Vpc(this, 'Wiki7Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
        },
      ],
    });

    // S3 Gateway Endpoint — free, avoids S3 traffic going through the internet
    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    // MediaWiki EC2 security group. (The `description` string below still says
    // ALB/containers from the Fargate era — GroupDescription is immutable in
    // CloudFormation, so correcting it would REPLACE the security group. Not
    // worth the disruption; the comment here is the source of truth.)
    this.mediawikiSecurityGroup = new ec2.SecurityGroup(this, 'MediaWikiSecurityGroup', {
      vpc: this.vpc,
      description: 'Allow inbound traffic from ALB to MediaWiki containers',
      allowAllOutbound: true,
    });

    // Database security group
    this.databaseSecurityGroup = new ec2.SecurityGroup(this, 'Wiki7DatabaseSecurityGroup', {
      vpc: this.vpc,
      description: 'Allow ECS containers to connect to MariaDB',
      allowAllOutbound: true,
    });

    // Allow the MediaWiki EC2 instance to connect to RDS (rule description kept
    // verbatim from the ECS era — see the immutability note above).
    this.databaseSecurityGroup.addIngressRule(
      this.mediawikiSecurityGroup,
      ec2.Port.tcp(3306),
      'Allow ECS MediaWiki containers to access RDS database'
    );
  }
}
