import { Construct } from 'constructs';
import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as logs from 'aws-cdk-lib/aws-logs';

interface ObservabilityStackProps {
  dbInstance: rds.DatabaseInstance;
  ec2InstanceId: string;
  distribution: cloudfront.Distribution;
  appLogGroup: logs.LogGroup;
}

/**
 * One place for all the "is anything wrong" signals.
 *
 * Five alarms cover the failure modes the existing status-check auto-recover
 * alarm doesn't catch:
 *   - DB filling up (silent killer; surfaces only on write failure)
 *   - DB or EC2 CPU pegged (could be runaway MW request, scraping, traffic)
 *   - CloudFront 5xx rate spike (origin or distribution misconfig)
 *   - MW container emitting Redis / Fatal / DB-connection errors
 *
 * Alarms have no actions wired up by default — they show as state changes in
 * the CloudWatch console and (when an SNS topic is added later) can notify.
 * Wiring email/Slack notifications is a Phase 4 follow-up; the alarms
 * themselves cost ~$0.30 each-per-month at most.
 */
export class ObservabilityStack extends Construct {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id);

    const { dbInstance, ec2InstanceId, distribution, appLogGroup } = props;

    // === RDS — free storage running out ======================================================
    // 5 GB threshold gives us a multi-week runway at our growth rate to bump
    // allocatedStorage before writes start failing.
    new cloudwatch.Alarm(this, 'RdsFreeStorageLow', {
      alarmName: 'wiki7-rds-free-storage-low',
      alarmDescription: 'RDS free storage < 5 GB — bump allocatedStorage before writes fail',
      metric: dbInstance.metricFreeStorageSpace({ period: cdk.Duration.minutes(5) }),
      threshold: 5 * 1024 * 1024 * 1024, // 5 GB
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // === RDS — CPU pegged ====================================================================
    new cloudwatch.Alarm(this, 'RdsCpuHigh', {
      alarmName: 'wiki7-rds-cpu-high',
      alarmDescription: 'RDS CPU > 85% sustained — runaway query or insufficient instance class',
      metric: dbInstance.metricCPUUtilization({ period: cdk.Duration.minutes(5) }),
      threshold: 85,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // === EC2 — CPU pegged ====================================================================
    new cloudwatch.Alarm(this, 'Ec2CpuHigh', {
      alarmName: 'wiki7-ec2-cpu-high',
      alarmDescription: 'EC2 CPU > 85% sustained — likely traffic spike or runaway PHP worker',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/EC2',
        metricName: 'CPUUtilization',
        dimensionsMap: { InstanceId: ec2InstanceId },
        period: cdk.Duration.minutes(5),
        statistic: 'Average',
      }),
      threshold: 85,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // === CloudFront — 5xx error rate =========================================================
    // 5% threshold is generous; real-world the rate should be < 0.1%.
    new cloudwatch.Alarm(this, 'CloudFront5xxHigh', {
      alarmName: 'wiki7-cloudfront-5xx-high',
      alarmDescription: 'CloudFront 5xx rate > 5% over 5 min — origin sick or distribution misconfig',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/CloudFront',
        metricName: '5xxErrorRate',
        dimensionsMap: { DistributionId: distribution.distributionId, Region: 'Global' },
        period: cdk.Duration.minutes(5),
        statistic: 'Average',
      }),
      threshold: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // === Application errors — log-derived metric =============================================
    // Match the patterns that genuinely indicate the wiki is misbehaving: PHP fatal errors,
    // Redis connection failures, DB connection failures, uncaught exceptions. Deliberately
    // does NOT match "PHP Notice" / "PHP Warning" / "Deprecated" — those are noisy and
    // not actionable in production.
    const errorFilter = new logs.MetricFilter(this, 'AppErrorFilter', {
      logGroup: appLogGroup,
      metricNamespace: 'Wiki7/Application',
      metricName: 'ErrorCount',
      filterPattern: logs.FilterPattern.anyTerm(
        'PHP Fatal',
        'PHP Parse error',
        'RedisException',
        'MWException',
        'DBConnectionError',
        'Out of memory',
        'segmentation fault',
      ),
      metricValue: '1',
      defaultValue: 0,
    });

    new cloudwatch.Alarm(this, 'AppErrorRateHigh', {
      alarmName: 'wiki7-app-errors-high',
      alarmDescription: 'MW container emitted > 5 fatal/connection errors in 5 min',
      metric: errorFilter.metric({
        period: cdk.Duration.minutes(5),
        statistic: 'Sum',
      }),
      threshold: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // === Redis sidecar down — tighter, faster signal ==========================================
    // If the Redis container dies, MW falls back to the DB (slow but functional) and every
    // request through RedisBagOStuff emits "RedisException". Distinct from the general error
    // alarm because (a) it's actionable on its own — restart the container — and (b) we want a
    // faster threshold than the bundled error count.
    //
    // We watch the MW stream rather than the Redis stream itself: an exited container produces
    // no logs, so absence-of-logs is unreliable in CloudWatch; presence of MW's RedisException
    // is the cleanest positive signal.
    const redisExceptionFilter = new logs.MetricFilter(this, 'RedisExceptionFilter', {
      logGroup: appLogGroup,
      metricNamespace: 'Wiki7/Application',
      metricName: 'RedisExceptionCount',
      filterPattern: logs.FilterPattern.anyTerm(
        'RedisException',
        'Could not connect to Redis',
        'Connection refused',
      ),
      metricValue: '1',
      defaultValue: 0,
    });
    new cloudwatch.Alarm(this, 'RedisSidecarDown', {
      alarmName: 'wiki7-redis-sidecar-down',
      alarmDescription: 'MW logged RedisException > 3 times in 5 min — Redis sidecar likely down',
      metric: redisExceptionFilter.metric({
        period: cdk.Duration.minutes(5),
        statistic: 'Sum',
      }),
      threshold: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
  }
}
