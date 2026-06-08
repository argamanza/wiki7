import * as path from 'path';
import { Construct } from 'constructs';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { DockerImageAsset } from 'aws-cdk-lib/aws-ecr-assets';

interface ComputeStackProps {
  vpc: ec2.Vpc;
  dbInstance: rds.DatabaseInstance;
  dbSecret: secretsmanager.Secret;
  mediawikiSecurityGroup: ec2.SecurityGroup;
  domainName: string;
}

export class ComputeStack extends Construct {
  // The Elastic IP — CloudFront origin (via the ec2.<domain> A-record) points here.
  readonly elasticIp: ec2.CfnEIP;
  readonly mediawikiStorageBucket: s3.Bucket;
  // Exposed so the ObservabilityStack can attach metric filters + alarms.
  readonly appLogGroup: logs.LogGroup;
  readonly instance: ec2.Instance;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id);

    const { vpc, dbInstance, dbSecret, mediawikiSecurityGroup, domainName } = props;
    const region = cdk.Stack.of(this).region;

    // === MediaWiki application secrets ========================================================
    // Three retained Secrets. Originally one Secret with a JSON template of three fields, but
    // `generateStringKey` only auto-generates ONE field — so secretKey and upgradeKey stayed
    // empty strings forever and LocalSettings.php's `getenv(...) ?: 'dev-only-...'` silently
    // fell through to the dev placeholders visible in this public repo (Phase 2.5c Finding 1,
    // exploitable for CSRF / session forgery). Splitting into three Secrets with one
    // auto-generated value each gets all three filled by CFN at create time; the rotation
    // choreography in revival-plan §Phase 2.5d puts a fresh value into all four secrets
    // (these three + the RDS-credentials secret) so the dev-placeholder values that ran in
    // prod between PR #24 and #44 are also rotated out.
    //
    // RETAIN on all three so a stack replacement doesn't rotate keys out from under sessions.
    // Existing secret name kept verbatim so CFN doesn't try to replace the resource that
    // already holds the live adminPassword.
    const mediawikiSecret = new secretsmanager.Secret(this, 'Wiki7MediaWikiSecret', {
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ adminPassword: '' }),
        generateStringKey: 'adminPassword',
        excludePunctuation: true,
        passwordLength: 32,
      },
      description: 'MediaWiki admin password',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const secretKeySecret = new secretsmanager.Secret(this, 'Wiki7SecretKeySecret', {
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 32,
      },
      description: 'MediaWiki $wgSecretKey (CSRF tokens, session IDs, password reset tokens)',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const upgradeKeySecret = new secretsmanager.Secret(this, 'Wiki7UpgradeKeySecret', {
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 16,
      },
      description: 'MediaWiki $wgUpgradeKey (gates the mw-config/ web installer)',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Wiki7Bot — Phase 3a data-pipeline credential. Decoupled from compute: NOT granted to the
    // EC2 instance role and NOT threaded into UserData / the docker --env-file. The container
    // never holds this value; only the pipeline runner (Tzahi's laptop, or a future CI runner)
    // fetches it via `aws secretsmanager get-secret-value` and exports WIKI_BOT_USER /
    // WIKI_BOT_PASS, which `data/run_pipeline.py` reads. The MW user is created out-of-band via
    // `php maintenance/run.php createAndPromote --custom-groups=bot --force Wiki7Bot <pass>`
    // (SSM Run Command against the live container) — see data/BOT_SETUP.md for the recipe.
    // The `bot` group carries `noratelimit` (documented at LocalSettings.php §rate-limits) so
    // a Phase 3a bulk import doesn't trip the per-user 90 edits/min bucket. RETAIN so a future
    // restack doesn't invalidate the live wiki user's password.
    new secretsmanager.Secret(this, 'Wiki7BotSecret', {
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'Wiki7Bot' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
      description: 'Wiki7Bot MediaWiki bot account password (Phase 3a data pipeline)',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Wiki7TelegramBotSecret — Phase 3.5 review-gate Telegram dispatch credential.
    //
    // Telegram bot tokens are issued by @BotFather out-of-band and can't be auto-generated
    // by CDK. The secret is created with an empty placeholder; the real token is populated
    // post-deploy with `aws secretsmanager put-secret-value`. RETAIN so a future restack
    // doesn't drop the token and require another BotFather rotation.
    //
    // Read by the EC2 instance role and threaded into the container env-file as
    // WIKI7_TELEGRAM_BOT_TOKEN; the Wiki7ReviewGate extension's Telegram dispatcher reads
    // it via PHP's getenv() and POSTs to api.telegram.org/bot<token>/sendMessage. When the
    // token is empty (initial deploy, before the post-deploy put-secret-value), the
    // dispatcher silently no-ops; Echo in-wiki notifications still fire.
    //
    // The chat_id (the Telegram user / group / channel to deliver messages to) is NOT
    // in the secret — it's not sensitive — and lives in $wgWiki7TelegramChatId in
    // docker/LocalSettings.php instead.
    const telegramBotSecret = new secretsmanager.Secret(this, 'Wiki7TelegramBotSecret', {
      secretStringValue: cdk.SecretValue.unsafePlainText(JSON.stringify({ botToken: '' })),
      description: 'Telegram bot token used by Wiki7ReviewGate to dispatch review-pending notifications (Phase 3.5)',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // === S3 bucket for MediaWiki uploads (read by CloudFront via OAC) ==========================
    this.mediawikiStorageBucket = new s3.Bucket(this, 'Wiki7StorageBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      // BUCKET_OWNER_ENFORCED disables ACLs entirely — only bucket policy + IAM grant access.
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.HEAD],
          allowedOrigins: [`https://${domainName}`, `https://www.${domainName}`],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
      lifecycleRules: [
        {
          id: 'ExpireOldVersions',
          enabled: true,
          noncurrentVersionExpiration: cdk.Duration.days(7),
          expiredObjectDeleteMarker: true,
        },
      ],
    });

    // Sync the docker/assets/ directory (logos, future static brand assets) into the bucket
    // on every deploy. Replaces the old hand-rolled `aws s3 sync` postdeploy script.
    new s3deploy.BucketDeployment(this, 'DeployAssets', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../docker/assets'))],
      destinationBucket: this.mediawikiStorageBucket,
      destinationKeyPrefix: 'assets',
      // Don't blow away uploads or other prefixes — only manage what we ship from docker/assets.
      prune: false,
      cacheControl: [s3deploy.CacheControl.fromString('public, max-age=86400')],
    });

    // Seed the bucket with `images/` and `assets/` prefixes the AWS S3 ext expects.
    const seedFn = new lambda.Function(this, 'S3DirectoriesLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 's3_directories.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/s3-directories')),
      timeout: cdk.Duration.seconds(30),
    });
    this.mediawikiStorageBucket.grantPut(seedFn);

    new cr.AwsCustomResource(this, 'CreateS3Directories', {
      onCreate: {
        service: 'Lambda',
        action: 'invoke',
        parameters: {
          FunctionName: seedFn.functionName,
          Payload: JSON.stringify({
            RequestType: 'Create',
            ResourceProperties: {
              BucketName: this.mediawikiStorageBucket.bucketName,
              Directories: ['assets', 'images'],
            },
          }),
        },
        physicalResourceId: cr.PhysicalResourceId.of('S3DirectoriesResource'),
      },
      onUpdate: {
        service: 'Lambda',
        action: 'invoke',
        parameters: {
          FunctionName: seedFn.functionName,
          Payload: JSON.stringify({
            RequestType: 'Update',
            ResourceProperties: {
              BucketName: this.mediawikiStorageBucket.bucketName,
              Directories: ['assets', 'images'],
            },
          }),
        },
        physicalResourceId: cr.PhysicalResourceId.of('S3DirectoriesResource'),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['lambda:InvokeFunction'],
          resources: [seedFn.functionArn],
        }),
      ]),
    });

    // === Container log group (Docker writes here via the awslogs driver) =======================
    // Holds two streams: 'mediawiki' (MW container) and 'redis' (sidecar). Same retention
    // for both so an incident timeline can correlate cache misbehavior with MW errors.
    const logGroup = new logs.LogGroup(this, 'Wiki7AppLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
    });
    // Expose the log group so the ObservabilityStack can attach a metric filter.
    this.appLogGroup = logGroup;

    // === Docker image asset — CDK builds for ARM64, pushes to ECR ==============================
    // The image URI hash baked into UserData below. When the image changes, UserData changes,
    // CloudFormation replaces the instance, and the new instance boots the new image.
    const image = new DockerImageAsset(this, 'Wiki7Image', {
      directory: path.join(__dirname, '../../docker'),
      platform: Platform.LINUX_ARM64,
    });

    // === Instance IAM role =====================================================================
    const instanceRole = new iam.Role(this, 'Wiki7InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'IAM role for the wiki7 EC2 instance',
    });
    // SSM Session Manager (replaces SSH; no port 22).
    instanceRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'));
    // Read secrets at boot.
    dbSecret.grantRead(instanceRole);
    mediawikiSecret.grantRead(instanceRole);
    secretKeySecret.grantRead(instanceRole);
    upgradeKeySecret.grantRead(instanceRole);
    telegramBotSecret.grantRead(instanceRole);
    // Pull the container image from CDK's ECR repo.
    image.repository.grantPull(instanceRole);
    // Write container logs to CloudWatch.
    logGroup.grantWrite(instanceRole);
    // Read + write uploads via the AWS S3 MediaWiki extension (no ACLs — BUCKET_OWNER_ENFORCED).
    instanceRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject', 's3:GetObject', 's3:DeleteObject', 's3:ListBucket', 's3:GetBucketLocation'],
      resources: [
        this.mediawikiStorageBucket.bucketArn,
        `${this.mediawikiStorageBucket.bucketArn}/*`,
      ],
    }));

    // === Security group for the EC2 instance ===================================================
    // Only CloudFront's edge IPs may reach port 80 — the instance is not directly reachable from
    // the public internet, even though it has a public EIP.
    const cloudFrontPrefixList = ec2.PrefixList.fromLookup(this, 'CloudFrontPrefixList', {
      prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
    });
    mediawikiSecurityGroup.addIngressRule(
      ec2.Peer.prefixList(cloudFrontPrefixList.prefixListId),
      ec2.Port.tcp(80),
      'HTTP from CloudFront edge only',
    );

    // === Elastic IP — stable hostname for the CloudFront origin ================================
    // Allocated first; UserData attaches it to the instance once the instance is healthy.
    this.elasticIp = new ec2.CfnEIP(this, 'Wiki7EIP', {
      domain: 'vpc',
      tags: [{ key: 'Name', value: 'wiki7-eip' }],
    });

    // === UserData — installs Docker, pulls the image, runs it ==================================
    const dbSecretArn = dbSecret.secretArn;
    const mwSecretArn = mediawikiSecret.secretArn;
    const secretKeyArn = secretKeySecret.secretArn;
    const upgradeKeyArn = upgradeKeySecret.secretArn;
    const telegramBotArn = telegramBotSecret.secretArn;
    const imageUri = image.imageUri;
    const ecrRegistry = `${cdk.Stack.of(this).account}.dkr.ecr.${region}.amazonaws.com`;
    const bucketName = this.mediawikiStorageBucket.bucketName;
    const logGroupName = logGroup.logGroupName;
    const eipAllocId = this.elasticIp.attrAllocationId;

    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      `#!/bin/bash`,
      `set -euxo pipefail`,
      ``,
      `# 1. Base packages — docker + jq for parsing Secrets Manager JSON, cronie for the`,
      `#    out-of-band job runner (see §6 below). AL2023 ships without cron.`,
      `dnf -y update`,
      `dnf -y install docker jq awscli cronie`,
      `systemctl enable --now docker`,
      `systemctl enable --now crond`,
      `# SSM Agent is preinstalled on AL2023; nothing to do.`,
      ``,
      `# 2. Self-attach the Elastic IP so this instance has a stable public address.`,
      `INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $(curl -sX PUT \\`,
      `  -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token)" \\`,
      `  http://169.254.169.254/latest/meta-data/instance-id)`,
      `aws ec2 associate-address \\`,
      `  --region ${region} \\`,
      `  --instance-id "$INSTANCE_ID" \\`,
      `  --allocation-id ${eipAllocId} \\`,
      `  --allow-reassociation`,
      ``,
      `# 3. Authenticate the Docker daemon to CDK's ECR repo and pull the image.`,
      `#    Retry both pulls — il-central-1 ECR endpoint occasionally resets the`,
      `#    TLS connection mid-pull (~5 sec into the manifest fetch), and Docker`,
      `#    Hub rate-limits anonymous pulls from cloud IP ranges.`,
      `aws ecr get-login-password --region ${region} | \\`,
      `  docker login --username AWS --password-stdin ${ecrRegistry}`,
      `for i in 1 2 3 4 5; do`,
      `  if docker pull ${imageUri}; then break; fi`,
      `  echo "MW image pull attempt $i failed; sleeping 15s..."`,
      `  sleep 15`,
      `done`,
      `for i in 1 2 3 4 5; do`,
      `  if docker pull redis:7-alpine; then break; fi`,
      `  echo "Redis image pull attempt $i failed; sleeping 15s..."`,
      `  sleep 15`,
      `done`,
      ``,
      `# 3a. Create a shared docker network so MW can reach Redis by container name.`,
      `docker network create wiki7-net 2>/dev/null || true`,
      ``,
      `# 3b. Sidecar Redis — cache only (no persistence), 256 MB cap, LRU eviction.`,
      `#     If Redis dies, MW falls back to the DB; cache rebuilds on first request.`,
      `#     Logs ship to the shared CloudWatch group under stream 'redis' so incident timelines`,
      `#     can correlate cache misbehavior (OOM evictions, hung connections) with MW errors.`,
      `docker rm -f redis 2>/dev/null || true`,
      `docker run -d \\`,
      `  --name redis \\`,
      `  --network wiki7-net \\`,
      `  --restart=always \\`,
      `  --memory=320m \\`,
      `  --log-driver=awslogs \\`,
      `  --log-opt awslogs-region=${region} \\`,
      `  --log-opt awslogs-group=${logGroupName} \\`,
      `  --log-opt awslogs-stream=redis \\`,
      `  redis:7-alpine \\`,
      `  redis-server --save "" --maxmemory 256mb --maxmemory-policy allkeys-lru`,
      ``,
      `# 4. Fetch secrets at boot and stage them in a chmod 0600 env-file.`,
      `#    Why a file instead of inline 'docker run -e KEY=VALUE': UserData runs with`,
      `#    'set -euxo pipefail', so xtrace echoes every command (including the docker`,
      `#    run line and any 'export FOO=$(jq ...)' lines) AFTER variable expansion`,
      `#    into /var/log/cloud-init-output.log AND the mediawiki CloudWatch stream`,
      `#    (cloud-init output ships there via the awslogs driver). That leaked the`,
      `#    DB password and admin password in the clear — Phase 2.5c Round 1 Finding 2.`,
      `#    Wrapping the value-writing block in 'set +x' silences the echo; reading the`,
      `#    values into a file the daemon picks up via --env-file means even a future`,
      `#    regression (someone re-enables xtrace) can't put them back on the docker`,
      `#    run command line. The file is chmod 0600 (root-only) and deleted after`,
      `#    'docker run' returns; the values live only in the container's process env.`,
      `ENVFILE=/tmp/wiki7.env`,
      `install -m 0600 /dev/null "$ENVFILE"`,
      `{ set +x; } 2>/dev/null`,
      `DB_JSON=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${dbSecretArn} --query SecretString --output text)`,
      `MW_JSON=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${mwSecretArn} --query SecretString --output text)`,
      `WG_SECRET_KEY_VAL=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${secretKeyArn} --query SecretString --output text)`,
      `WG_UPGRADE_KEY_VAL=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${upgradeKeyArn} --query SecretString --output text)`,
      `TG_JSON=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${telegramBotArn} --query SecretString --output text)`,
      `{`,
      `  printf 'MEDIAWIKI_DB_HOST=%s\\n' '${dbInstance.dbInstanceEndpointAddress}'`,
      `  printf 'MEDIAWIKI_DB_NAME=wikidb\\n'`,
      `  printf 'MEDIAWIKI_DB_USER=wikiuser\\n'`,
      `  printf 'MEDIAWIKI_DB_PASSWORD=%s\\n' "$(echo "$DB_JSON" | jq -r .password)"`,
      `  printf 'MEDIAWIKI_ADMIN_PASSWORD=%s\\n' "$(echo "$MW_JSON" | jq -r .adminPassword)"`,
      `  printf 'WG_SECRET_KEY=%s\\n' "$WG_SECRET_KEY_VAL"`,
      `  printf 'WG_UPGRADE_KEY=%s\\n' "$WG_UPGRADE_KEY_VAL"`,
      `  printf 'WIKI7_TELEGRAM_BOT_TOKEN=%s\\n' "$(echo "$TG_JSON" | jq -r .botToken)"`,
      `  printf 'WIKI_ENV=production\\n'`,
      `  printf 'S3_BUCKET_NAME=%s\\n' '${bucketName}'`,
      `  printf 'REDIS_HOST=redis\\n'`,
      `} >> "$ENVFILE"`,
      `set -x`,
      ``,
      `# 5. Run the MW container on the same network so it can reach 'redis:6379'.`,
      `#    All env vars (secret + non-secret) come from --env-file so the docker run`,
      `#    line in cloud-init's log carries no values.`,
      `docker rm -f wiki7 2>/dev/null || true`,
      `docker run -d \\`,
      `  --name wiki7 \\`,
      `  --network wiki7-net \\`,
      `  --restart=always \\`,
      `  -p 80:80 \\`,
      `  --env-file "$ENVFILE" \\`,
      `  --log-driver=awslogs \\`,
      `  --log-opt awslogs-region=${region} \\`,
      `  --log-opt awslogs-group=${logGroupName} \\`,
      `  --log-opt awslogs-stream=mediawiki \\`,
      `  ${imageUri}`,
      `# Values are now in the container's process namespace; the on-host file is no`,
      `# longer needed and is removed so a later disk-image read can't recover it.`,
      `rm -f "$ENVFILE"`,
      ``,
      `# 6. MediaWiki job-queue runner — drains the queue every minute via host cron.`,
      `#    LocalSettings.php sets $wgJobRunRate = 0 so jobs are NOT executed inline on`,
      `#    web requests (avoids latency tax + would drain too slowly anyway since most`,
      `#    requests hit CloudFront edge cache and never reach the origin). --maxtime=55`,
      `#    bounds each invocation comfortably under the 60s cron interval so back-to-back`,
      `#    runs can't overlap. stdout is dropped (jobs are noisy by design); stderr is`,
      `#    appended to /var/log/wiki7-jobrunner.err for greppability via SSM Session Manager.`,
      `cat > /etc/cron.d/wiki7-jobrunner <<'EOF'`,
      `* * * * * root /usr/bin/docker exec wiki7 php maintenance/run.php runJobs --maxtime=55 >/dev/null 2>>/var/log/wiki7-jobrunner.err`,
      `EOF`,
      `chmod 0644 /etc/cron.d/wiki7-jobrunner`,
    );

    // === The instance ==========================================================================
    const instance = new ec2.Instance(this, 'Wiki7Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      // t4g.small: 2 vCPU Graviton ARM64, 2 GB RAM. Enough headroom for MediaWiki + opcache.
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.SMALL),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64,
      }),
      securityGroup: mediawikiSecurityGroup,
      role: instanceRole,
      userData,
      // Replace the instance — and re-run UserData — when UserData changes (i.e. image hash).
      userDataCausesReplacement: true,
      requireImdsv2: true,
      // Encrypted gp3 root volume.
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: ec2.BlockDeviceVolume.ebs(30, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,
            deleteOnTermination: true,
          }),
        },
      ],
    });
    // No `disableApiTermination` here on purpose. The EC2 is stateless — the EBS root is
    // restored from the AMI on every deploy and there's no irreplaceable data on it. Termination
    // protection was originally added as a guard against `cdk destroy`, but in practice it
    // actively breaks every UserData-driven deploy (CFN's replacement-delete trips the flag and
    // the whole stack rolls back). The actual "do not destroy" data lives in RDS, which keeps
    // `deletionProtection: true`.
    // Allow the instance role to associate the EIP to itself in UserData.
    instanceRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ec2:AssociateAddress', 'ec2:DescribeAddresses', 'ec2:DescribeInstances'],
      resources: ['*'], // EC2 read APIs don't accept resource-level ARNs
    }));

    // === Auto-recover on hardware failure ======================================================
    // The status-check alarm triggers `ec2.recover` — AWS moves the instance to healthy hardware
    // within minutes, keeping the same instance ID, EIP, and EBS volume.
    new cloudwatch.Alarm(this, 'StatusCheckRecoverAlarm', {
      metric: new cloudwatch.Metric({
        namespace: 'AWS/EC2',
        metricName: 'StatusCheckFailed_System',
        dimensionsMap: { InstanceId: instance.instanceId },
        period: cdk.Duration.minutes(1),
        statistic: 'Maximum',
      }),
      threshold: 0,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 2,
      alarmDescription: 'Recover the instance when AWS detects underlying hardware failure',
    }).addAlarmAction(new cwActions.Ec2Action(cwActions.Ec2InstanceAction.RECOVER));

    this.instance = instance;

    // === SSM Patch Manager — weekly OS patching ================================================
    // Without this, AL2023 doesn't auto-apply security updates and the OS slowly rots. The
    // maintenance window runs Sunday 02:30 IDT (Sat 23:30 UTC) — a day after the Friday-night
    // RDS window so they never overlap. Targets this single instance by ID. RebootIfNeeded=true
    // keeps the EC2 alarm system in scope when kernel updates land.
    const patchWindow = new ssm.CfnMaintenanceWindow(this, 'PatchMaintenanceWindow', {
      name: 'wiki7-weekly-patch-window',
      description: 'Weekly OS patching for the wiki7 EC2 — Sunday 02:30 IDT (Sat 23:30 UTC)',
      schedule: 'cron(30 23 ? * SAT *)', // Saturday 23:30 UTC = Sunday 02:30 IDT (end of Israeli weekend)
      scheduleTimezone: 'UTC',
      duration: 2,
      cutoff: 0,
      allowUnassociatedTargets: false,
    });
    const patchTarget = new ssm.CfnMaintenanceWindowTarget(this, 'PatchTarget', {
      windowId: patchWindow.ref,
      resourceType: 'INSTANCE',
      targets: [{ key: 'InstanceIds', values: [instance.instanceId] }],
    });
    new ssm.CfnMaintenanceWindowTask(this, 'PatchTask', {
      windowId: patchWindow.ref,
      targets: [{ key: 'WindowTargetIds', values: [patchTarget.ref] }],
      taskArn: 'AWS-RunPatchBaseline',
      taskType: 'RUN_COMMAND',
      maxConcurrency: '1',
      maxErrors: '0',
      priority: 1,
      taskInvocationParameters: {
        maintenanceWindowRunCommandParameters: {
          parameters: { Operation: ['Install'], RebootOption: ['RebootIfNeeded'] },
          documentVersion: '$LATEST',
          timeoutSeconds: 3600,
        },
      },
    });

    // === Sitemap-generation SSM Document ======================================================
    // Trigger via `aws ssm send-command --document-name Wiki7-GenerateSitemap --targets ...`.
    // Generates the sitemap inside the MW container, copies it out to the host, and uploads to
    // S3 under `assets/sitemap/`. Reachable at:
    //   https://wiki7.co.il/assets/sitemap/sitemap-index-wikidb.xml
    // (the existing CloudFront `/assets/*` behavior serves S3 via OAC).
    //
    // Two bugs in the earlier revision worth flagging in case future-self touches this:
    //   1. --urlpath must be a PATH ('/assets/sitemap/'), not a full URL. generateSitemap
    //      prepends --server to it, so passing 'https://wiki7.co.il/...' produced doubled
    //      URLs ('https://wiki7.co.il/https://wiki7.co.il/...') in the sitemap index.
    //   2. The S3 destination must be 'assets/sitemap/' (under the assets/ prefix that
    //      the BucketDeployment + CloudFront /assets/* behavior share); plain 'sitemap/'
    //      uploaded to the bucket root, which CloudFront's /assets/* route can't reach.
    //
    // Also removed --content-type application/xml from the sync. It was set uniformly for
    // both the .xml index and the .xml.gz sub-sitemaps, mislabeling the gzipped file
    // (which should be application/x-gzip + Content-Encoding: gzip). Letting awscli
    // auto-detect from extension gets both right.
    //
    // Manual until content is curated and we know what's worth indexing. Phase 4 wires a
    // weekly EventBridge schedule.
    new ssm.CfnDocument(this, 'GenerateSitemapDoc', {
      name: 'Wiki7-GenerateSitemap',
      documentType: 'Command',
      documentFormat: 'YAML',
      // Without this, CFN's default 'Replace' update method tries to delete + recreate
      // the document on every content change. Since we use a custom Name, the recreate
      // step collides with the existing document and CFN rolls the whole stack back.
      // 'NewVersion' makes CFN bump the document to a new version in place - no rename
      // dance, no rollback. Lesson learned from PR #33's failed deploy.
      updateMethod: 'NewVersion',
      content: [
        'schemaVersion: "2.2"',
        'description: Generate the MediaWiki sitemap and upload to the S3 storage bucket',
        'parameters: {}',
        'mainSteps:',
        '  - action: aws:runShellScript',
        '    name: generateSitemap',
        '    inputs:',
        '      runCommand:',
        '        - set -eux',
        '        - rm -rf /tmp/wiki7-sitemap && mkdir -p /tmp/wiki7-sitemap',
        '        - docker exec wiki7 bash -c "mkdir -p /tmp/sitemap && rm -f /tmp/sitemap/* && php maintenance/run.php generateSitemap --fspath=/tmp/sitemap/ --urlpath=/assets/sitemap/ --server=https://wiki7.co.il --identifier=wikidb"',
        '        - docker cp wiki7:/tmp/sitemap/. /tmp/wiki7-sitemap/',
        `        - aws s3 sync /tmp/wiki7-sitemap/ s3://${this.mediawikiStorageBucket.bucketName}/assets/sitemap/ --region ${region} --delete`,
        '        - ls -la /tmp/wiki7-sitemap',
      ].join('\n'),
    });

    // Outputs for ops + debugging.
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'ElasticIp', { value: this.elasticIp.ref });
    new cdk.CfnOutput(this, 'StorageBucketName', { value: this.mediawikiStorageBucket.bucketName });
  }
}
