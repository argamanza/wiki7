import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { DatabaseStack } from '../lib/database-stack';
import { ApplicationStack } from '../lib/application-stack';
import { BackupStack } from '../lib/backup-stack';
import { Wiki7WafStack } from '../lib/wiki7-waf-stack';

// Helper: create a stack with NetworkStack construct
function createNetworkStack(): { stack: cdk.Stack; network: NetworkStack } {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestStack');
  const network = new NetworkStack(stack, 'Network');
  return { stack, network };
}

function createDatabaseStack(): { stack: cdk.Stack; network: NetworkStack; database: DatabaseStack } {
  const { stack, network } = createNetworkStack();
  const database = new DatabaseStack(stack, 'Database', {
    vpc: network.vpc,
    databaseSecurityGroup: network.databaseSecurityGroup,
    mediawikiSecurityGroup: network.mediawikiSecurityGroup,
  });
  return { stack, network, database };
}

describe('NetworkStack', () => {
  let template: Template;

  beforeAll(() => {
    const { stack } = createNetworkStack();
    template = Template.fromStack(stack);
  });

  test('creates a VPC', () => {
    template.resourceCountIs('AWS::EC2::VPC', 1);
  });

  test('creates public and private subnets', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      MapPublicIpOnLaunch: true,
    });
  });

  test('has no NAT gateway (cost optimization)', () => {
    template.resourceCountIs('AWS::EC2::NatGateway', 0);
  });

  test('creates S3 VPC gateway endpoint', () => {
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
      ServiceName: Match.objectLike({}),
      VpcEndpointType: 'Gateway',
    });
  });

  test('creates MediaWiki security group', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: Match.stringLikeRegexp('.*MediaWiki.*'),
    });
  });

  test('creates Database security group', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: Match.stringLikeRegexp('.*MariaDB.*'),
    });
  });

  test('allows ECS to connect to RDS on port 3306', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
      IpProtocol: 'tcp',
      FromPort: 3306,
      ToPort: 3306,
    });
  });
});

describe('DatabaseStack', () => {
  let template: Template;

  beforeAll(() => {
    const { stack } = createDatabaseStack();
    template = Template.fromStack(stack);
  });

  test('creates RDS instance', () => {
    template.resourceCountIs('AWS::RDS::DBInstance', 1);
  });

  test('uses MariaDB engine, version 11.4', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      Engine: 'mariadb',
      EngineVersion: Match.stringLikeRegexp('^11\\.4'),
    });
  });

  test('uses Graviton t4g.micro instance class', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      DBInstanceClass: 'db.t4g.micro',
    });
  });

  test('enables storage encryption', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      StorageEncrypted: true,
    });
  });

  test('has deletion protection enabled', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      DeletionProtection: true,
    });
  });

  test('takes a final snapshot on stack delete', () => {
    template.hasResource('AWS::RDS::DBInstance', {
      DeletionPolicy: 'Snapshot',
      UpdateReplacePolicy: 'Snapshot',
    });
  });

  test('attaches the dedicated database security group (not the MW SG)', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      VPCSecurityGroups: Match.arrayWith([
        Match.objectLike({
          'Fn::GetAtt': Match.arrayWith([
            Match.stringLikeRegexp('.*Wiki7DatabaseSecurityGroup.*'),
            'GroupId',
          ]),
        }),
      ]),
    });
  });

  test('creates Secrets Manager secret for DB credentials', () => {
    template.hasResourceProperties('AWS::SecretsManager::Secret', {
      Description: 'Database credentials for Wiki7 MediaWiki database',
    });
  });
});

describe('ApplicationStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'TestAppStack');
    const network = new NetworkStack(stack, 'Network');
    const database = new DatabaseStack(stack, 'Database', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });
    new ApplicationStack(stack, 'Application', {
      vpc: network.vpc,
      dbInstance: database.dbInstance,
      dbSecret: database.dbSecret,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
      domainName: 'wiki7.co.il',
    });
    template = Template.fromStack(stack);
  });

  test('creates ECS cluster', () => {
    template.resourceCountIs('AWS::ECS::Cluster', 1);
  });

  test('creates Fargate task definition with ARM64/Graviton', () => {
    template.hasResourceProperties('AWS::ECS::TaskDefinition', {
      RequiresCompatibilities: ['FARGATE'],
      Cpu: '512',
      Memory: '1024',
      RuntimePlatform: {
        CpuArchitecture: 'ARM64',
        OperatingSystemFamily: 'LINUX',
      },
    });
  });

  test('creates ECS Fargate service with deployment circuit breaker', () => {
    template.hasResourceProperties('AWS::ECS::Service', {
      LaunchType: 'FARGATE',
      DesiredCount: 1,
      DeploymentConfiguration: Match.objectLike({
        DeploymentCircuitBreaker: {
          Enable: true,
          Rollback: true,
        },
      }),
    });
  });

  test('autoscaling target tracking on CPU is configured', () => {
    template.resourceCountIs('AWS::ApplicationAutoScaling::ScalableTarget', 1);
    template.hasResourceProperties('AWS::ApplicationAutoScaling::ScalingPolicy', {
      PolicyType: 'TargetTrackingScaling',
      TargetTrackingScalingPolicyConfiguration: Match.objectLike({
        TargetValue: 70,
        PredefinedMetricSpecification: { PredefinedMetricType: 'ECSServiceAverageCPUUtilization' },
      }),
    });
  });

  test('S3 storage bucket blocks ALL public access', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  test('S3 bucket enforces BucketOwner-only ownership (no ACLs)', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      OwnershipControls: {
        Rules: [{ ObjectOwnership: 'BucketOwnerEnforced' }],
      },
    });
  });

  test('S3 bucket has versioning enabled', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      VersioningConfiguration: { Status: 'Enabled' },
    });
  });

  test('task role policy does NOT grant s3:PutObjectAcl', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    for (const [, resource] of Object.entries(policies)) {
      const statements = (resource as { Properties: { PolicyDocument: { Statement: unknown[] } } })
        .Properties.PolicyDocument.Statement;
      for (const stmt of statements) {
        const actions = (stmt as { Action?: string | string[] }).Action;
        const actionList = Array.isArray(actions) ? actions : [actions];
        expect(actionList).not.toContain('s3:PutObjectAcl');
      }
    }
  });

  test('creates Application Load Balancer', () => {
    template.resourceCountIs('AWS::ElasticLoadBalancingV2::LoadBalancer', 1);
  });

  test('creates ALB listener on port 80', () => {
    template.hasResourceProperties('AWS::ElasticLoadBalancingV2::Listener', {
      Port: 80,
      Protocol: 'HTTP',
    });
  });

  test('health check uses MediaWiki API endpoint', () => {
    template.hasResourceProperties('AWS::ElasticLoadBalancingV2::TargetGroup', {
      HealthCheckPath: '/api.php?action=query&meta=siteinfo&format=json',
      Matcher: { HttpCode: '200' },
    });
  });

  test('ECS service has health check grace period', () => {
    template.hasResourceProperties('AWS::ECS::Service', {
      HealthCheckGracePeriodSeconds: 300,
    });
  });

  test('creates MediaWiki application secret', () => {
    template.hasResourceProperties('AWS::SecretsManager::Secret', {
      Description: 'MediaWiki application secrets (admin password, secret key, upgrade key)',
    });
  });

  test('container has MediaWiki secrets injected', () => {
    template.hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Secrets: Match.arrayWith([
            Match.objectLike({ Name: 'MEDIAWIKI_DB_PASSWORD' }),
            Match.objectLike({ Name: 'MEDIAWIKI_ADMIN_PASSWORD' }),
            Match.objectLike({ Name: 'WG_SECRET_KEY' }),
            Match.objectLike({ Name: 'WG_UPGRADE_KEY' }),
          ]),
        }),
      ]),
    });
  });
});

describe('BackupStack', () => {
  let template: Template;

  beforeAll(() => {
    const { stack } = createDatabaseStack();
    const network = new NetworkStack(stack, 'BackupNet');
    const database = new DatabaseStack(stack, 'BackupDb', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });
    new BackupStack(stack, 'Backup', {
      dbInstance: database.dbInstance,
    });
    template = Template.fromStack(stack);
  });

  test('creates backup vault', () => {
    template.resourceCountIs('AWS::Backup::BackupVault', 1);
  });

  test('creates backup plan', () => {
    template.resourceCountIs('AWS::Backup::BackupPlan', 1);
  });

  test('creates KMS key for backup encryption', () => {
    template.hasResourceProperties('AWS::KMS::Key', {
      EnableKeyRotation: true,
    });
  });
});

describe('Wiki7WafStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new Wiki7WafStack(app, 'TestWafStack', {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    template = Template.fromStack(stack);
  });

  test('creates a WebACL', () => {
    template.resourceCountIs('AWS::WAFv2::WebACL', 1);
  });

  test('AllowLegitimateBot has a lower priority than the BlockSuspicious rule', () => {
    const acls = template.findResources('AWS::WAFv2::WebACL');
    const webacl = Object.values(acls)[0] as { Properties: { Rules: Array<{ Name: string; Priority: number }> } };
    const allow = webacl.Properties.Rules.find(r => r.Name === 'AllowLegitimateBot');
    const block = webacl.Properties.Rules.find(r => r.Name === 'BlockSuspiciousMediaWikiPatterns');
    expect(allow).toBeDefined();
    expect(block).toBeDefined();
    expect(allow!.Priority).toBeLessThan(block!.Priority);
  });

  test('includes SQLi managed rule set', () => {
    const acls = template.findResources('AWS::WAFv2::WebACL');
    const webacl = Object.values(acls)[0] as { Properties: { Rules: Array<{ Name: string }> } };
    expect(webacl.Properties.Rules.map(r => r.Name)).toContain('AWS-AWSManagedRulesSQLiRuleSet');
  });

  test('includes PHP managed rule set', () => {
    const acls = template.findResources('AWS::WAFv2::WebACL');
    const webacl = Object.values(acls)[0] as { Properties: { Rules: Array<{ Name: string }> } };
    expect(webacl.Properties.Rules.map(r => r.Name)).toContain('AWS-AWSManagedRulesPHPRuleSet');
  });

  test('allow-bot list covers the major social/messaging crawlers', () => {
    const synth = template.toJSON();
    const synthJson = JSON.stringify(synth).toLowerCase();
    for (const term of ['googlebot', 'bingbot', 'applebot', 'facebookexternalhit', 'twitterbot', 'slackbot', 'discordbot']) {
      expect(synthJson).toContain(term);
    }
  });
});
