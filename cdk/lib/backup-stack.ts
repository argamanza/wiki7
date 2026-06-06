import { Construct } from 'constructs';
import * as cdk from 'aws-cdk-lib';
import * as backup from 'aws-cdk-lib/aws-backup';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as events from 'aws-cdk-lib/aws-events';
import * as kms from 'aws-cdk-lib/aws-kms';

interface BackupStackProps {
  dbInstance: rds.IDatabaseInstance;
}

export class BackupStack extends Construct {
  constructor(scope: Construct, id: string, props: BackupStackProps) {
    super(scope, id);

    const { dbInstance } = props;

    // Create a custom KMS key for backup encryption
    const backupKey = new kms.Key(this, 'Wiki7BackupKey', {
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Create a backup vault with encryption
    const backupVault = new backup.BackupVault(this, 'Wiki7BackupVault', {
      encryptionKey: backupKey,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Create a backup plan
    const backupPlan = new backup.BackupPlan(this, 'Wiki7BackupPlan', {
      backupVault: backupVault,
    });

    // Add backup rule: daily backups, keep for 7 days
    backupPlan.addRule(new backup.BackupPlanRule({
      ruleName: 'DailyBackup',
      scheduleExpression: events.Schedule.cron({ hour: '1', minute: '0' }), // Daily at 01:00 UTC
      deleteAfter: cdk.Duration.days(7),
    }));

    // Long-retention monthly snapshot — covers the >7-day "I deleted something
    // and didn't notice for a few weeks" scenario that the daily rule misses.
    // 1st of month at 02:00 UTC, kept for 1 year. ~$0.50/mo at our DB size.
    backupPlan.addRule(new backup.BackupPlanRule({
      ruleName: 'MonthlyLongRetention',
      scheduleExpression: events.Schedule.cron({ day: '1', hour: '2', minute: '0' }),
      deleteAfter: cdk.Duration.days(365),
    }));

    // Add resources to the backup plan - just the RDS instance
    backupPlan.addSelection('Wiki7BackupSelection', {
      resources: [
        backup.BackupResource.fromRdsDatabaseInstance(dbInstance),
      ],
      // This is required for the selection to be valid
      allowRestores: true,
    });
  }
}