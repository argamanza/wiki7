# Wiki7 Infrastructure (CDK Project)

This project defines the AWS infrastructure for the **Wiki7 MediaWiki site** using the AWS Cloud Development Kit (CDK) in TypeScript.

Architecture overview + request flow: see [`../docs/architecture.md`](../docs/architecture.md).
Why single-EC2 instead of Fargate+ALB: [`../docs/adr/0001-single-ec2-vs-fargate-alb.md`](../docs/adr/0001-single-ec2-vs-fargate-alb.md).

---

## Project Structure

```plaintext
cdk/
├── bin/
│   └── wiki7.ts                  # CDK App entry point (5 stacks, 2 regions)
├── lib/
│   ├── wiki7-cdk-stack.ts        # Main stack orchestrating the constructs below + GuardDuty
│   ├── network-stack.ts          # VPC (public-only, no NAT) + security groups
│   ├── database-stack.ts        # RDS MariaDB 11.4 (deletion-protected) + credentials secret
│   ├── compute-stack.ts          # EC2 + Docker UserData, app secrets, S3 uploads bucket,
│   │                             #   EIP, job-runner cron, patch window, sitemap SSM doc
│   ├── cloudfront-stack.ts       # CloudFront distribution + cache policies (Wiki7DynamicHtml)
│   ├── observability-stack.ts    # 6 alarms → SNS email + the `wiki7` dashboard
│   ├── backup-stack.ts           # AWS Backup vault + daily/monthly RDS rules
│   ├── wiki7-dns-stack.ts        # Route53 hosted zone + records
│   ├── wiki7-certificate-stack.ts# ACM cert (us-east-1, CloudFront requirement)
│   ├── wiki7-waf-stack.ts        # WAF WebACL (us-east-1, CLOUDFRONT scope)
│   ├── github-oidc-stack.ts      # GitHub Actions OIDC roles (deploy + read-only PR diff)
│   └── cross-region-ssm-sync.ts  # Copies cert/WAF ARNs us-east-1 → il-central-1 via SSM
├── test/cdk.test.ts              # Jest assertions (run: npm test)
├── cdk.json                      # Context: domain, alarmEmail, search-console token
└── package.json
```

## Common commands

```bash
npm ci && npm test     # Jest suite
make cdk-synth         # From repo root — synth with cached context, no AWS creds needed
make cdk-diff          # Diff against deployed stacks (needs AWS creds)
make cdk-deploy        # Deploy everything (CI does this on master pushes)
```
