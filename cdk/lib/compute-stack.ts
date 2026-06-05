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

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id);

    const { vpc, dbInstance, dbSecret, mediawikiSecurityGroup, domainName } = props;
    const region = cdk.Stack.of(this).region;

    // === MediaWiki application secrets (admin pw, $wgSecretKey, $wgUpgradeKey) =================
    // RETAIN so a stack replacement doesn't rotate keys out from under sessions.
    const mediawikiSecret = new secretsmanager.Secret(this, 'Wiki7MediaWikiSecret', {
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          adminPassword: '',
          secretKey: '',
          upgradeKey: '',
        }),
        generateStringKey: 'adminPassword',
        excludePunctuation: true,
        passwordLength: 32,
      },
      description: 'MediaWiki application secrets (admin password, secret key, upgrade key)',
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
    const logGroup = new logs.LogGroup(this, 'Wiki7AppLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
    });

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
      `# 1. Base packages — docker + jq for parsing Secrets Manager JSON.`,
      `dnf -y update`,
      `dnf -y install docker jq awscli`,
      `systemctl enable --now docker`,
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
      `aws ecr get-login-password --region ${region} | \\`,
      `  docker login --username AWS --password-stdin ${ecrRegistry}`,
      `docker pull ${imageUri}`,
      ``,
      `# 4. Fetch secrets at boot — never written to disk, only into the container's env.`,
      `DB_JSON=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${dbSecretArn} --query SecretString --output text)`,
      `MW_JSON=$(aws secretsmanager get-secret-value --region ${region} --secret-id ${mwSecretArn} --query SecretString --output text)`,
      `export MEDIAWIKI_DB_PASSWORD=$(echo "$DB_JSON" | jq -r .password)`,
      `export MEDIAWIKI_ADMIN_PASSWORD=$(echo "$MW_JSON" | jq -r .adminPassword)`,
      `export WG_SECRET_KEY=$(echo "$MW_JSON" | jq -r .secretKey)`,
      `export WG_UPGRADE_KEY=$(echo "$MW_JSON" | jq -r .upgradeKey)`,
      ``,
      `# 5. Run the container. --restart=always handles dockerd restarts and reboots.`,
      `docker rm -f wiki7 2>/dev/null || true`,
      `docker run -d \\`,
      `  --name wiki7 \\`,
      `  --restart=always \\`,
      `  -p 80:80 \\`,
      `  -e MEDIAWIKI_DB_HOST=${dbInstance.dbInstanceEndpointAddress} \\`,
      `  -e MEDIAWIKI_DB_NAME=wikidb \\`,
      `  -e MEDIAWIKI_DB_USER=wikiuser \\`,
      `  -e MEDIAWIKI_DB_PASSWORD="$MEDIAWIKI_DB_PASSWORD" \\`,
      `  -e MEDIAWIKI_ADMIN_PASSWORD="$MEDIAWIKI_ADMIN_PASSWORD" \\`,
      `  -e WG_SECRET_KEY="$WG_SECRET_KEY" \\`,
      `  -e WG_UPGRADE_KEY="$WG_UPGRADE_KEY" \\`,
      `  -e WIKI_ENV=production \\`,
      `  -e S3_BUCKET_NAME=${bucketName} \\`,
      `  --log-driver=awslogs \\`,
      `  --log-opt awslogs-region=${region} \\`,
      `  --log-opt awslogs-group=${logGroupName} \\`,
      `  --log-opt awslogs-stream=mediawiki \\`,
      `  ${imageUri}`,
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
    // Termination protection — can only be disabled via API/console, not a stray `cdk destroy`.
    (instance.node.defaultChild as ec2.CfnInstance).disableApiTermination = true;
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

    // Outputs for ops + debugging.
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'ElasticIp', { value: this.elasticIp.ref });
    new cdk.CfnOutput(this, 'StorageBucketName', { value: this.mediawikiStorageBucket.bucketName });
  }
}
