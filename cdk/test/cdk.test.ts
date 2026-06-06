import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { DatabaseStack } from '../lib/database-stack';
import { ComputeStack } from '../lib/compute-stack';
import { BackupStack } from '../lib/backup-stack';
import { Wiki7WafStack } from '../lib/wiki7-waf-stack';

const TEST_ENV = { account: '111111111111', region: 'il-central-1' };

function createNetworkStack(): { stack: cdk.Stack; network: NetworkStack } {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestStack', { env: TEST_ENV });
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

// =========================================================================================
describe('NetworkStack', () => {
  let template: Template;
  beforeAll(() => {
    template = Template.fromStack(createNetworkStack().stack);
  });

  test('creates a VPC', () => template.resourceCountIs('AWS::EC2::VPC', 1));
  test('public subnets get public IPs', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', { MapPublicIpOnLaunch: true });
  });
  test('no NAT gateways (cost)', () => template.resourceCountIs('AWS::EC2::NatGateway', 0));
  test('S3 gateway endpoint', () => {
    template.hasResourceProperties('AWS::EC2::VPCEndpoint', { VpcEndpointType: 'Gateway' });
  });
  test('separate MediaWiki + Database SGs', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: Match.stringLikeRegexp('.*MediaWiki.*'),
    });
    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: Match.stringLikeRegexp('.*MariaDB.*'),
    });
  });
  test('MW SG can reach DB on 3306', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
      IpProtocol: 'tcp', FromPort: 3306, ToPort: 3306,
    });
  });
});

// =========================================================================================
describe('DatabaseStack', () => {
  let template: Template;
  beforeAll(() => {
    template = Template.fromStack(createDatabaseStack().stack);
  });

  test('RDS instance', () => template.resourceCountIs('AWS::RDS::DBInstance', 1));
  test('MariaDB 11.4 LTS', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      Engine: 'mariadb',
      EngineVersion: Match.stringLikeRegexp('^11\\.4'),
    });
  });
  test('Graviton t4g.micro', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', { DBInstanceClass: 'db.t4g.micro' });
  });
  test('storage encrypted', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', { StorageEncrypted: true });
  });
  test('deletion protection ON', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', { DeletionProtection: true });
  });
  test('takes a final snapshot on delete', () => {
    template.hasResource('AWS::RDS::DBInstance', {
      DeletionPolicy: 'Snapshot',
      UpdateReplacePolicy: 'Snapshot',
    });
  });
  test('attached to dedicated DB SG, NOT the MW SG', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      VPCSecurityGroups: Match.arrayWith([
        Match.objectLike({
          'Fn::GetAtt': Match.arrayWith([
            Match.stringLikeRegexp('.*Wiki7DatabaseSecurityGroup.*'), 'GroupId',
          ]),
        }),
      ]),
    });
  });
  test('credentials secret retained', () => {
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: 'Database credentials for Wiki7 MediaWiki database',
      }),
      DeletionPolicy: 'Retain',
    });
  });
  test('maintenance window is ddd:hh:mm-ddd:hh:mm (day-prefixed)', () => {
    // RDS rejects this format silently at synth time but with a 400 at deploy time;
    // assert the shape so a regression is caught by CI, not by a rolled-back stack.
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      PreferredMaintenanceWindow: Match.stringLikeRegexp(
        '^(mon|tue|wed|thu|fri|sat|sun):[0-2][0-9]:[0-5][0-9]-(mon|tue|wed|thu|fri|sat|sun):[0-2][0-9]:[0-5][0-9]$',
      ),
    });
  });
  test('backup window is hh:mm-hh:mm (NO day prefix)', () => {
    // The day-prefixed form is valid for maintenance windows but invalid for backup windows;
    // RDS returns 'Invalid backup window time' and rolls the stack back. This test exists
    // because that exact failure broke the first post-merge deploy of PR #24.
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      PreferredBackupWindow: Match.stringLikeRegexp('^[0-2][0-9]:[0-5][0-9]-[0-2][0-9]:[0-5][0-9]$'),
    });
  });
});

// =========================================================================================
describe('ComputeStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'TestComputeStack', { env: TEST_ENV });
    const network = new NetworkStack(stack, 'Network');
    const database = new DatabaseStack(stack, 'Database', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });
    new ComputeStack(stack, 'Compute', {
      vpc: network.vpc,
      dbInstance: database.dbInstance,
      dbSecret: database.dbSecret,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
      domainName: 'wiki7.co.il',
    });
    template = Template.fromStack(stack);
  });

  test('exactly one EC2 instance', () => {
    template.resourceCountIs('AWS::EC2::Instance', 1);
  });

  test('Graviton t4g.small ARM64', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      InstanceType: 't4g.small',
    });
  });

  test('IMDSv2 enforced', () => {
    template.hasResourceProperties('AWS::EC2::LaunchTemplate', {
      LaunchTemplateData: Match.objectLike({
        MetadataOptions: Match.objectLike({ HttpTokens: 'required' }),
      }),
    });
  });

  test('termination protection OFF (CFN must be able to replace the instance on UserData change)', () => {
    // DisableApiTermination=true blocks CFN's replacement-delete and rolls the whole stack back
    // on every UserData change. The EC2 is stateless; the irreplaceable data lives in RDS,
    // which keeps `deletionProtection: true`. Regression guard for the 2026-06-06 stuck-deploy
    // incident (5 orphan instances were left running after rollback failures).
    template.hasResourceProperties('AWS::EC2::Instance', {
      DisableApiTermination: Match.absent(),
    });
  });

  test('root EBS volume is encrypted gp3', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      BlockDeviceMappings: Match.arrayWith([
        Match.objectLike({
          Ebs: Match.objectLike({ Encrypted: true, VolumeType: 'gp3' }),
        }),
      ]),
    });
  });

  test('static EIP allocated', () => {
    template.resourceCountIs('AWS::EC2::EIP', 1);
  });

  test('status-check alarm + auto-recover action', () => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      MetricName: 'StatusCheckFailed_System',
      Namespace: 'AWS/EC2',
      ComparisonOperator: 'GreaterThanThreshold',
    });
    // The recover action ARN is built via Fn::Join — verify the literal :ec2:recover suffix.
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const synth = JSON.stringify(Object.values(alarms)[0]);
    expect(synth).toContain(':ec2:recover');
  });

  test('S3 bucket: BLOCK_ALL public access', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  test('S3 bucket: BucketOwner-enforced (ACLs disabled)', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      OwnershipControls: { Rules: [{ ObjectOwnership: 'BucketOwnerEnforced' }] },
    });
  });

  test('S3 bucket: versioning ON', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      VersioningConfiguration: { Status: 'Enabled' },
    });
  });

  test('S3 bucket retained on stack delete', () => {
    template.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
  });

  test('instance role does NOT grant s3:PutObjectAcl', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    for (const [, resource] of Object.entries(policies)) {
      const statements = (resource as { Properties: { PolicyDocument: { Statement: unknown[] } } })
        .Properties.PolicyDocument.Statement;
      for (const stmt of statements) {
        const actions = (stmt as { Action?: string | string[] }).Action;
        const actionList = Array.isArray(actions) ? actions : actions ? [actions] : [];
        expect(actionList).not.toContain('s3:PutObjectAcl');
      }
    }
  });

  test('instance role has SSM managed instance policy', () => {
    template.hasResourceProperties('AWS::IAM::Role', {
      ManagedPolicyArns: Match.arrayWith([
        Match.objectLike({
          'Fn::Join': Match.arrayWith([
            '',
            Match.arrayWith([Match.stringLikeRegexp('.*AmazonSSMManagedInstanceCore')]),
          ]),
        }),
      ]),
    });
  });

  test('MediaWiki app secret retained', () => {
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: 'MediaWiki application secrets (admin password, secret key, upgrade key)',
      }),
      DeletionPolicy: 'Retain',
    });
  });

  test('CloudWatch log group for container logs', () => {
    template.resourceCountIs('AWS::Logs::LogGroup', 1);
  });
});

// =========================================================================================
describe('BackupStack', () => {
  let template: Template;
  beforeAll(() => {
    const { stack, network } = createNetworkStack();
    const database = new DatabaseStack(stack, 'Database', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });
    new BackupStack(stack, 'Backup', { dbInstance: database.dbInstance });
    template = Template.fromStack(stack);
  });

  test('backup vault', () => template.resourceCountIs('AWS::Backup::BackupVault', 1));
  test('backup plan', () => template.resourceCountIs('AWS::Backup::BackupPlan', 1));
  test('KMS key with rotation', () => {
    template.hasResourceProperties('AWS::KMS::Key', { EnableKeyRotation: true });
  });
});

// =========================================================================================
describe('Wiki7WafStack', () => {
  let template: Template;
  beforeAll(() => {
    const app = new cdk.App();
    const stack = new Wiki7WafStack(app, 'TestWafStack', {
      env: { account: '111111111111', region: 'us-east-1' },
    });
    template = Template.fromStack(stack);
  });

  test('WebACL exists', () => template.resourceCountIs('AWS::WAFv2::WebACL', 1));

  test('AllowLegitimateBot priority < BlockSuspiciousMediaWikiPatterns priority', () => {
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

  test('allow list covers the major social/messaging crawlers', () => {
    const synthJson = JSON.stringify(template.toJSON()).toLowerCase();
    for (const term of [
      'googlebot', 'bingbot', 'applebot', 'facebookexternalhit',
      'twitterbot', 'slackbot', 'discordbot',
    ]) {
      expect(synthJson).toContain(term);
    }
  });
});
