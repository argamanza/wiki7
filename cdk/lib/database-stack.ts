import { Construct } from 'constructs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as cdk from 'aws-cdk-lib';

interface DatabaseStackProps {
  vpc: ec2.Vpc;
  // SG attached to the DB instance (separate from the MW SG; ingress is granted from the MW SG)
  databaseSecurityGroup: ec2.SecurityGroup;
  // SG attached to the MediaWiki EC2 instance — used as the ingress source for port 3306
  mediawikiSecurityGroup: ec2.SecurityGroup;
}

export class DatabaseStack extends Construct {
  readonly dbInstance: rds.DatabaseInstance;
  readonly dbSecret: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: DatabaseStackProps) {
    super(scope, id);

    const { vpc, databaseSecurityGroup, mediawikiSecurityGroup } = props;

    this.dbSecret = new secretsmanager.Secret(this, 'Wiki7DatabaseSecret', {
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'wikiuser' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        includeSpace: false,
      },
      description: 'Database credentials for Wiki7 MediaWiki database',
      // Retain the secret if the stack is replaced — avoids rotating creds out from under a live DB.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.dbInstance = new rds.DatabaseInstance(this, 'Wiki7Database', {
      engine: rds.DatabaseInstanceEngine.mariaDb({ version: rds.MariaDbEngineVersion.VER_11_4_9 }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [databaseSecurityGroup],
      credentials: rds.Credentials.fromSecret(this.dbSecret),
      multiAz: false,
      allocatedStorage: 20,
      maxAllocatedStorage: 100,
      // Graviton t4g.micro — cheapest Graviton class supported for MariaDB.
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO),
      publiclyAccessible: false,
      // The hard lesson from the prior teardown: keep the data protected against accidental destroy.
      removalPolicy: cdk.RemovalPolicy.SNAPSHOT,
      deletionProtection: true,
      backupRetention: cdk.Duration.days(7),
      databaseName: 'wikidb',
      storageEncrypted: true,
    });

    this.dbInstance.connections.allowFrom(
      mediawikiSecurityGroup,
      ec2.Port.tcp(3306),
      'Allow MediaWiki EC2 instance to connect to RDS',
    );
  }
}
