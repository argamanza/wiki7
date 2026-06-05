import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as path from 'path';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as cdk from 'aws-cdk-lib';
import {Tags} from "aws-cdk-lib";

interface ApplicationStackProps {
  vpc: ec2.Vpc;
  dbInstance: rds.DatabaseInstance;
  dbSecret: secretsmanager.Secret;
  mediawikiSecurityGroup: ec2.SecurityGroup;
  domainName: string;
}

export class ApplicationStack extends Construct {
  readonly alb: elbv2.ApplicationLoadBalancer;
  readonly mediawikiStorageBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: ApplicationStackProps) {
    super(scope, id);

    const { vpc, dbInstance, dbSecret, mediawikiSecurityGroup, domainName } = props;

    // Create ECS Cluster — Container Insights disabled to save costs
    const cluster = new ecs.Cluster(this, 'Wiki7Cluster', {
      vpc,
    });

    // IAM Role for ECS tasks
    const taskRole = new iam.Role(this, 'Wiki7TaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'IAM role for MediaWiki ECS containers',
    });

    taskRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy')
    );

    dbSecret.grantRead(taskRole);

    // Create Secrets Manager secret for MediaWiki application secrets
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

    mediawikiSecret.grantRead(taskRole);

    // Create S3 bucket for MediaWiki storage.
    // CloudFront reaches the bucket via OAC, so public access is unnecessary and is fully locked down.
    this.mediawikiStorageBucket = new s3.Bucket(this, 'Wiki7StorageBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      // BUCKET_OWNER_ENFORCED disables ACLs entirely — the only access path is via bucket policy + IAM.
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      cors: [
        {
          allowedMethods: [
            s3.HttpMethods.GET,
            s3.HttpMethods.HEAD,
          ],
          allowedOrigins: [
            `https://${domainName}`, 
            `https://www.${domainName}`
          ], // Restrict to your domains
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
        }
      ],
    });

    // Grant ECS task role access to S3
    this.mediawikiStorageBucket.grantReadWrite(taskRole);

    // IAM policy for ECS task role.
    // BUCKET_OWNER_ENFORCED disables ACLs entirely, so s3:PutObjectAcl is intentionally absent.
    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:PutObject',
        's3:GetObject',
        's3:DeleteObject',
        's3:ListBucket',
        's3:GetBucketLocation',
      ],
      resources: [
        this.mediawikiStorageBucket.bucketArn,
        `${this.mediawikiStorageBucket.bucketArn}/*`,
      ],
    }));
    
    // Create a Lambda function to initialize S3 directories
    const s3DirectoriesLambdaRole = new iam.Role(this, 'S3DirectoriesLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
      ],
    });
    
    // Add S3 permissions to the Lambda role
    s3DirectoriesLambdaRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:PutObject'],
      resources: [
        `${this.mediawikiStorageBucket.bucketArn}/*`,
      ],
    }));
    
    // Create the Lambda function
    const s3DirectoriesFunction = new lambda.Function(this, 'S3DirectoriesLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 's3_directories.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/s3-directories')),
      timeout: cdk.Duration.seconds(30),
      role: s3DirectoriesLambdaRole,
    });
    
    // Create the custom resource to invoke the Lambda
    new cr.AwsCustomResource(this, 'CreateS3Directories', {
      onCreate: {
        service: 'Lambda',
        action: 'invoke',
        parameters: {
          FunctionName: s3DirectoriesFunction.functionName,
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
          FunctionName: s3DirectoriesFunction.functionName,
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
      onDelete: {
        service: 'Lambda',
        action: 'invoke',
        parameters: {
          FunctionName: s3DirectoriesFunction.functionName,
          Payload: JSON.stringify({
            RequestType: 'Delete',
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
          resources: [s3DirectoriesFunction.functionArn],
        }),
      ]),
    });

    // Log Group for container logs
    const logGroup = new logs.LogGroup(this, 'Wiki7LogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
    });

    // Task Definition — Graviton/ARM64 for ~20% cost saving + better perf-per-dollar.
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'Wiki7TaskDef', {
      cpu: 512,
      memoryLimitMiB: 1024,
      taskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    const container = taskDefinition.addContainer('MediaWikiContainer', {
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../docker'), {
        platform: Platform.LINUX_ARM64,
      }),
      logging: ecs.LogDriver.awsLogs({
        logGroup,
        streamPrefix: 'mediawiki',
      }),
      environment: {
        MEDIAWIKI_DB_HOST: dbInstance.dbInstanceEndpointAddress,
        MEDIAWIKI_DB_NAME: 'wikidb',
        MEDIAWIKI_DB_USER: 'wikiuser',
        WIKI_ENV: 'production',
        S3_BUCKET_NAME: this.mediawikiStorageBucket.bucketName,
      },
    });

    container.addSecret('MEDIAWIKI_DB_PASSWORD', ecs.Secret.fromSecretsManager(dbSecret, 'password'));
    container.addSecret('MEDIAWIKI_ADMIN_PASSWORD', ecs.Secret.fromSecretsManager(mediawikiSecret, 'adminPassword'));
    container.addSecret('WG_SECRET_KEY', ecs.Secret.fromSecretsManager(mediawikiSecret, 'secretKey'));
    container.addSecret('WG_UPGRADE_KEY', ecs.Secret.fromSecretsManager(mediawikiSecret, 'upgradeKey'));

    container.addPortMappings({
      containerPort: 80,
      protocol: ecs.Protocol.TCP,
    });

    // Create Fargate Service.
    // circuitBreaker.rollback auto-reverts to the prior task definition on a failed deploy
    // — the cheap form of auto-heal for a single-task service.
    const fargateService = new ecs.FargateService(this, 'Wiki7Service', {
      cluster,
      taskDefinition,
      desiredCount: 1,
      assignPublicIp: true,
      securityGroups: [mediawikiSecurityGroup],
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      healthCheckGracePeriod: cdk.Duration.seconds(300),
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      circuitBreaker: { rollback: true },
    });

    // Target-tracking autoscaling: keeps cost ≈ 1 task at idle, expands to 3 under load.
    const scaling = fargateService.autoScaleTaskCount({ minCapacity: 1, maxCapacity: 3 });
    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(120),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // ALB Security Group
    const albSecurityGroup = new ec2.SecurityGroup(this, 'Wiki7AlbSecurityGroup', {
      vpc,
      description: 'Allow HTTP traffic to ALB from anywhere',
      allowAllOutbound: true,
    });

    albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'Allow HTTP traffic from anywhere');

    // Application Load Balancer
    this.alb = new elbv2.ApplicationLoadBalancer(this, 'Wiki7Alb', {
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      loadBalancerName: 'Wiki7Alb',
    });

    // Listener and Target Group
    const listener = this.alb.addListener('Wiki7AlbListener', {
      port: 80,
      open: true,
    });

    listener.addTargets('Wiki7AlbTargets', {
      port: 80,
      targets: [fargateService],
      healthCheck: {
        path: '/api.php?action=query&meta=siteinfo&format=json',
        healthyHttpCodes: '200',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 5,
      },
    });
    
    // Output the S3 bucket name
    new cdk.CfnOutput(this, 'MediaWikiStorageBucketName', {
      value: this.mediawikiStorageBucket.bucketName,
      description: 'S3 bucket name for MediaWiki storage',
    });
  }
}