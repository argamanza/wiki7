import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { DatabaseStack } from '../lib/database-stack';
import { ComputeStack } from '../lib/compute-stack';
import { BackupStack } from '../lib/backup-stack';
import { Wiki7WafStack } from '../lib/wiki7-waf-stack';
import { CloudFrontConstruct } from '../lib/cloudfront-stack';

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

  test('MediaWiki admin-password secret retained', () => {
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({ Description: 'MediaWiki admin password' }),
      DeletionPolicy: 'Retain',
    });
  });

  test('MediaWiki $wgSecretKey secret retained (Phase 2.5d Finding 1)', () => {
    // Regression guard: a previous design used a single Secret with a JSON template
    // {adminPassword,secretKey,upgradeKey} + generateStringKey: 'adminPassword', which
    // only auto-generates the keyed field — secretKey/upgradeKey stayed empty strings
    // and LocalSettings.php silently ran on its dev placeholders in prod.
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: Match.stringLikeRegexp('.*\\$wgSecretKey.*'),
        GenerateSecretString: Match.objectLike({ PasswordLength: 32, ExcludePunctuation: true }),
      }),
      DeletionPolicy: 'Retain',
    });
  });

  test('MediaWiki $wgUpgradeKey secret retained (Phase 2.5d Finding 1)', () => {
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: Match.stringLikeRegexp('.*\\$wgUpgradeKey.*'),
        GenerateSecretString: Match.objectLike({ PasswordLength: 16, ExcludePunctuation: true }),
      }),
      DeletionPolicy: 'Retain',
    });
  });

  test('Wiki7Telegram secret retained + threaded into UserData env-file (Phase 3.5)', () => {
    // The Telegram bot token is sensitive but the chat_id is not. The token
    // lives in a retained Secret (placeholder at create time, populated
    // post-deploy by `aws secretsmanager put-secret-value`); the chat_id is
    // hardcoded in docker/LocalSettings.php. The container must receive the
    // token via the env-file pattern (Phase 2.5d) — never inline `-e` flags.
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: Match.stringLikeRegexp('.*Telegram.*'),
      }),
      DeletionPolicy: 'Retain',
    });
    // Decoupling guard for the token's path into the container: UserData must
    // contain the WIKI7_TELEGRAM_BOT_TOKEN env-var emission AND must reference
    // the Telegram secret ARN (so the boot script can fetch the token value).
    const instances = template.findResources('AWS::EC2::Instance');
    const userDataB64 = JSON.stringify(Object.values(instances)[0]);
    expect(userDataB64).toContain('WIKI7_TELEGRAM_BOT_TOKEN');
    expect(userDataB64).toContain('TG_JSON');
    // The bot token must NOT appear inline on the docker-run command line —
    // that would leak it to /var/log/cloud-init-output.log + CloudWatch.
    // Same Phase 2.5d Finding 2 guarantee applied to the new secret.
    expect(userDataB64).toMatch(/--env-file/);
  });

  test('Wiki7Bot secret retained, decoupled from compute (Phase 3a)', () => {
    // The bot credential lives next to the other MW secrets but is intentionally NOT granted
    // to the EC2 instance role and NOT threaded into UserData — the container never holds it.
    // The pipeline runner fetches it directly via Secrets Manager.
    template.hasResource('AWS::SecretsManager::Secret', {
      Properties: Match.objectLike({
        Description: Match.stringLikeRegexp('.*Wiki7Bot.*'),
        GenerateSecretString: Match.objectLike({
          PasswordLength: 32,
          ExcludePunctuation: true,
          GenerateStringKey: 'password',
          SecretStringTemplate: Match.stringLikeRegexp('.*"username":"Wiki7Bot".*'),
        }),
      }),
      DeletionPolicy: 'Retain',
    });
    // Decoupling guard: the EC2 UserData must NOT reference the bot secret's logical id or any
    // BOT_USER / BOT_PASS env-var name. If a future change tries to thread the bot creds through
    // the container env-file, this assertion fails and forces a conscious re-decision.
    const instances = template.findResources('AWS::EC2::Instance');
    const userDataB64 = JSON.stringify(Object.values(instances)[0]);
    expect(userDataB64).not.toContain('Wiki7BotSecret');
    expect(userDataB64).not.toContain('WIKI_BOT_USER');
    expect(userDataB64).not.toContain('WIKI_BOT_PASS');
    expect(userDataB64).not.toContain('MEDIAWIKI_BOT_USER');
    expect(userDataB64).not.toContain('MEDIAWIKI_BOT_PASSWORD');
  });

  test('UserData uses docker run --env-file (Phase 2.5d Finding 2)', () => {
    // Inline -e SECRET="$VAR" leaks values to /var/log/cloud-init-output.log AND to the
    // mediawiki CloudWatch stream because UserData runs under `set -x` and bash echoes
    // post-expansion arguments. The env-file pattern keeps both values and command line
    // out of the log. Regression guard for Phase 2.5c Round 1 Finding 2.
    const instances = template.findResources('AWS::EC2::Instance');
    const userDataB64 = JSON.stringify(Object.values(instances)[0]);
    expect(userDataB64).toContain('--env-file');
    // The env-file path is constructed in UserData; assert the staging file name appears.
    expect(userDataB64).toContain('/tmp/wiki7.env');
  });

  test('UserData does NOT pass secrets via inline -e flags (Phase 2.5d Finding 2)', () => {
    // Defensive regression check. The values themselves never appear at synth time (the
    // variables expand at boot), but the literal `-e VAR=` shape would be a tell-tale of
    // a regression to the old design.
    const instances = template.findResources('AWS::EC2::Instance');
    const userDataStr = JSON.stringify(Object.values(instances)[0]);
    for (const env of [
      '-e MEDIAWIKI_DB_PASSWORD',
      '-e MEDIAWIKI_ADMIN_PASSWORD',
      '-e WG_SECRET_KEY',
      '-e WG_UPGRADE_KEY',
    ]) {
      expect(userDataStr).not.toContain(env);
    }
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

// =========================================================================================
describe('CloudFrontConstruct — Wiki7DynamicHtml cache policy', () => {
  // The cookie allowList is the regression-sensitive part of Phase 2.5b: a future refactor
  // that "cleans up" the list could silently break either correctness (logged-in HTML leaking
  // to anon) or cache hit ratio (adding wikidb_session would shatter anon cache to one entry
  // per visitor). These tests lock the decisions in and document WHY each cookie is in or
  // out of the cache key.
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'TestCloudFrontStack', { env: TEST_ENV });
    const network = new NetworkStack(stack, 'Network');
    const database = new DatabaseStack(stack, 'Database', {
      vpc: network.vpc,
      databaseSecurityGroup: network.databaseSecurityGroup,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
    });
    const compute = new ComputeStack(stack, 'Compute', {
      vpc: network.vpc,
      dbInstance: database.dbInstance,
      dbSecret: database.dbSecret,
      mediawikiSecurityGroup: network.mediawikiSecurityGroup,
      domainName: 'wiki7.co.il',
    });
    const hostedZone = route53.HostedZone.fromHostedZoneAttributes(stack, 'StubZone', {
      hostedZoneId: 'Z0000000000000000000',
      zoneName: 'wiki7.co.il',
    });
    const certificate = acm.Certificate.fromCertificateArn(
      stack,
      'StubCert',
      'arn:aws:acm:us-east-1:111111111111:certificate/00000000-0000-0000-0000-000000000000',
    );
    new CloudFrontConstruct(stack, 'CloudFront', {
      originElasticIp: compute.elasticIp,
      hostedZone,
      certificate,
      domainName: 'wiki7.co.il',
      mediawikiStorageBucket: compute.mediawikiStorageBucket,
      wafWebAclArn:
        'arn:aws:wafv2:us-east-1:111111111111:global/webacl/Wiki7/00000000-0000-0000-0000-000000000000',
    });
    template = Template.fromStack(stack);
  });

  // Locate the dynamic-HTML cache policy by its stable Name. Returns the CFN resource
  // properties object so each test can assert against it cleanly.
  function findDynamicHtmlPolicy(): {
    Name: string;
    ParametersInCacheKeyAndForwardedToOrigin: {
      CookiesConfig: { CookieBehavior: string; Cookies: string[] };
      HeadersConfig: { HeaderBehavior: string; Headers: string[] };
      QueryStringsConfig: { QueryStringBehavior: string };
      EnableAcceptEncodingGzip: boolean;
      EnableAcceptEncodingBrotli: boolean;
    };
  } {
    const policies = template.findResources('AWS::CloudFront::CachePolicy');
    const match = Object.values(policies).find(p =>
      (p as { Properties: { CachePolicyConfig: { Name?: string } } }).Properties.CachePolicyConfig?.Name
        === 'Wiki7DynamicHtml',
    );
    expect(match).toBeDefined();
    return (match as { Properties: { CachePolicyConfig: ReturnType<typeof findDynamicHtmlPolicy> } })
      .Properties.CachePolicyConfig;
  }

  test('Wiki7DynamicHtml cache policy exists', () => {
    template.hasResourceProperties('AWS::CloudFront::CachePolicy', {
      CachePolicyConfig: Match.objectLike({ Name: 'Wiki7DynamicHtml' }),
    });
  });

  test('cookie allowList contains exactly the three auth-bearing cookies', () => {
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.CookiesConfig.CookieBehavior).toBe('whitelist');
    expect([...cfg.ParametersInCacheKeyAndForwardedToOrigin.CookiesConfig.Cookies].sort())
      .toEqual(['sessionJwt', 'wikidbToken', 'wikidbUserID']);
  });

  test('cookie allowList does NOT include wikidb_session (would explode anon cache fragmentation)', () => {
    // MW's CookieSessionProvider sets `<prefix>_session` on any persisted session — including
    // anon notice dismissals, edit-page views, CSRF token issuance. If we keyed on it, every
    // visitor with any session state would land on a unique cache entry — defeating the cache.
    // Wikimedia's Varnish VCL excludes _session for the same reason.
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.CookiesConfig.Cookies).not.toContain('wikidb_session');
  });

  test('cookie allowList does NOT include wikidbUserName (retained post-logout as login hint, not auth)', () => {
    // MW keeps UserName around after logout to pre-fill the login form on next visit
    // (see CookieSessionProvider.php REL1_45); its presence does NOT indicate an active
    // session. Keying on it would give every previously-logged-in visitor their own anon
    // cache entry — same content as no-cookie anon, pure waste.
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.CookiesConfig.Cookies).not.toContain('wikidbUserName');
  });

  test('query string behavior = all (MW URLs vary on ?action=, ?oldid=, ?diff=, …)', () => {
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.QueryStringsConfig.QueryStringBehavior).toBe('all');
  });

  test('header allowList = Accept-Language only', () => {
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.HeadersConfig.HeaderBehavior).toBe('whitelist');
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.HeadersConfig.Headers).toEqual(['Accept-Language']);
  });

  test('brotli + gzip encoding enabled', () => {
    const cfg = findDynamicHtmlPolicy();
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.EnableAcceptEncodingBrotli).toBe(true);
    expect(cfg.ParametersInCacheKeyAndForwardedToOrigin.EnableAcceptEncodingGzip).toBe(true);
  });

  test('default behavior uses Wiki7DynamicHtml, NOT the managed CACHING_DISABLED', () => {
    // CACHING_DISABLED's managed ID is 4135ea2d-6df8-44a3-9df3-4b5a84be39ad. Before Phase 2.5b
    // the distribution was hard-wired to it, so $wgUseCdn / s-maxage emission was wasted at
    // the edge. Guard against a regression that would silently restore that broken state.
    // The synthesized CachePolicyId is a CFN `Ref` to the policy resource's logical ID,
    // which CDK derives from the construct ID 'DynamicHtmlCachePolicy'.
    const dists = template.findResources('AWS::CloudFront::Distribution');
    const dist = Object.values(dists)[0] as {
      Properties: { DistributionConfig: { DefaultCacheBehavior: { CachePolicyId: unknown } } };
    };
    const policyIdJson = JSON.stringify(dist.Properties.DistributionConfig.DefaultCacheBehavior.CachePolicyId);
    expect(policyIdJson).not.toContain('4135ea2d-6df8-44a3-9df3-4b5a84be39ad');
    expect(policyIdJson).toContain('DynamicHtmlCachePolicy');
  });
});
