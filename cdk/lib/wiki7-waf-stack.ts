import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import {CrossRegionSsmSync} from "./cross-region-ssm-sync";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as logs from 'aws-cdk-lib/aws-logs';

interface Wiki7WafStackProps extends cdk.StackProps {
}

// Common substrings in legitimate crawler User-Agents. Lowercase + CONTAINS, so e.g.
// "googlebot" matches "Googlebot/2.1 (+http://www.google.com/bot.html)".
const ALLOWED_BOT_TERMS = [
  'googlebot',
  'bingbot',
  'applebot',
  'duckduckbot',
  'slackbot',
  'discordbot',
  'twitterbot',
  'facebookexternalhit',
  'linkedinbot',
  'pinterestbot',
  'embedly',
  'telegrambot',
  'whatsapp',
  // Monitoring — UptimeRobot's UA is
  //   "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)"
  // which contains "bot" and would otherwise trip the priority-8 bot-heuristic
  // block. UA-spoofing is theoretically possible but bounded by the per-IP rate
  // limit (priority 6, evaluated BEFORE this allow) and the short, fixed
  // request shape (GET /).
  'uptimerobot',
];

function botUserAgentMatch(term: string): wafv2.CfnWebACL.StatementProperty {
  return {
    byteMatchStatement: {
      searchString: term,
      fieldToMatch: { singleHeader: { name: 'User-Agent' } },
      textTransformations: [{ priority: 0, type: 'LOWERCASE' }],
      positionalConstraint: 'CONTAINS',
    },
  };
}

export class Wiki7WafStack extends cdk.Stack {
  readonly webAcl: wafv2.CfnWebACL;

  constructor(scope: Construct, id: string, props: Wiki7WafStackProps) {
    super(scope, id, {
      ...props,
      env: {
        region: 'us-east-1',
        account: props.env?.account,
      },
    });

    this.webAcl = new wafv2.CfnWebACL(this, 'Wiki7WebAcl', {
      defaultAction: { allow: {} },
      scope: 'CLOUDFRONT',
      visibilityConfig: {
        sampledRequestsEnabled: true,
        cloudWatchMetricsEnabled: true,
        metricName: 'Wiki7WebAcl',
      },
      description: 'WAF for Wiki7 MediaWiki site',
      rules: [
        // 1. Geo-block first — cheapest filter.
        {
          name: 'BlockCertainCountries',
          priority: 1,
          statement: {
            geoMatchStatement: {
              countryCodes: [
                'AF', 'DZ', 'BD', 'BY', 'CN', 'CU', 'IR', 'IQ', 'KP',
                'LB', 'LY', 'PK', 'RU', 'SY', 'YE', 'VE', 'VN',
              ],
            },
          },
          action: { block: {} },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'GeoBlock',
            sampledRequestsEnabled: true,
          },
        },
        // 2. AWS managed: Core rule set.
        {
          name: 'AWS-AWSManagedRulesCommonRuleSet',
          priority: 2,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesCommonRuleSet',
              excludedRules: [
                { name: 'SizeRestrictions_BODY' },        // MW POST bodies (uploads, large edits)
                { name: 'SizeRestrictions_QUERYSTRING' }, // long search queries
                { name: 'CrossSiteScripting_BODY' },     // false-positive on image uploads
              ],
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'AWS-AWSManagedRulesCommonRuleSet',
          },
        },
        // 3. AWS managed: Known bad inputs.
        {
          name: 'AWS-AWSManagedRulesKnownBadInputsRuleSet',
          priority: 3,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesKnownBadInputsRuleSet',
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'AWS-AWSManagedRulesKnownBadInputsRuleSet',
          },
        },
        // 4. AWS managed: SQL injection (MediaWiki is a MySQL/MariaDB app).
        {
          name: 'AWS-AWSManagedRulesSQLiRuleSet',
          priority: 4,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesSQLiRuleSet',
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'AWS-AWSManagedRulesSQLiRuleSet',
          },
        },
        // 5. AWS managed: PHP-specific attacks (MediaWiki is PHP).
        {
          name: 'AWS-AWSManagedRulesPHPRuleSet',
          priority: 5,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesPHPRuleSet',
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'AWS-AWSManagedRulesPHPRuleSet',
          },
        },
        // 6. Rate limit — 2000 requests / 5 min per IP. Evaluated BEFORE the
        //    crawler allow rule: a terminating allow skips every later rule, so
        //    if this ran after AllowLegitimateBot, anyone spoofing a crawler UA
        //    (e.g. "Googlebot") would be exempt from rate limiting entirely.
        {
          name: 'RateLimitPerIP',
          priority: 6,
          action: { block: {} },
          statement: {
            rateBasedStatement: {
              limit: 2000,
              aggregateKeyType: 'IP',
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'RateLimitPerIP',
          },
        },
        // 7. Allow legitimate crawlers BEFORE the bot-heuristic block at priority 8.
        //    Allow is terminating — its only job is to shield real crawlers from
        //    rule 8's generic "bot" UA heuristic; rate limiting already happened.
        {
          name: 'AllowLegitimateBot',
          priority: 7,
          action: { allow: {} },
          statement: {
            orStatement: {
              statements: ALLOWED_BOT_TERMS.map(botUserAgentMatch),
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'AllowLegitimateBot',
          },
        },
        // 8. Heuristic block: anything with /../ in the path, or generic bot-like UA.
        //    Legitimate crawlers were already allowed at priority 6.
        {
          name: 'BlockSuspiciousMediaWikiPatterns',
          priority: 8,
          action: { block: {} },
          statement: {
            orStatement: {
              statements: [
                {
                  byteMatchStatement: {
                    searchString: '..',
                    fieldToMatch: { uriPath: {} },
                    textTransformations: [{ priority: 0, type: 'URL_DECODE' }],
                    positionalConstraint: 'CONTAINS',
                  },
                },
                {
                  regexMatchStatement: {
                    regexString: '.*(bot|crawl|spider|scan).*',
                    fieldToMatch: { singleHeader: { name: 'User-Agent' } },
                    textTransformations: [{ priority: 0, type: 'LOWERCASE' }],
                  },
                },
              ],
            },
          },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: 'BlockSuspiciousMediaWikiPatterns',
          },
        },
      ],
    });

    const wafLogGroup = new logs.LogGroup(this, 'WafLogGroup', {
      logGroupName: 'aws-waf-logs-wiki7',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    new wafv2.CfnLoggingConfiguration(this, 'Wiki7WafLogging', {
      resourceArn: this.webAcl.attrArn,
      logDestinationConfigs: [
        cdk.Stack.of(this).formatArn({
          service: 'logs',
          region: 'us-east-1',
          account: cdk.Stack.of(this).account,
          resource: 'log-group',
          resourceName: wafLogGroup.logGroupName,
          arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
        }),
      ],
      loggingFilter: {
        DefaultBehavior: 'DROP',
        Filters: [
          {
            Behavior: 'KEEP',
            Requirement: 'MEETS_ALL',
            Conditions: [
              {
                ActionCondition: {
                  Action: 'BLOCK',
                },
              },
            ],
          },
        ],
      },
    });

    new ssm.StringParameter(this, 'Wiki7WafWebAclArnParameter', {
      parameterName: '/wiki7/waf-webacl/arn',
      stringValue: this.webAcl.attrArn,
    });

    new CrossRegionSsmSync(this, 'WafSync', {
      parameterName: '/wiki7/waf-webacl/arn',
      sourceRegion: 'us-east-1',
      targetRegion: 'il-central-1',
    });
  }
}
