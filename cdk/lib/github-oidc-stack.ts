import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';

export class GitHubOidcStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const account = this.account;

    const oidcProvider = new iam.OpenIdConnectProvider(this, 'GitHubOidcProvider', {
      url: 'https://token.actions.githubusercontent.com',
      clientIds: ['sts.amazonaws.com'],
      thumbprints: ['6938fd4d98bab03faadb97b34396831e3780aea1'],
    });

    const regions = ['il-central-1', 'us-east-1'];
    const bootstrapRoleArns = (prefixes: string[]) =>
      regions.flatMap((region) =>
        prefixes.map(
          (prefix) => `arn:aws:iam::${account}:role/cdk-hnb659fds-${prefix}-role-${account}-${region}`
        )
      );

    // === Deploy role — master pushes + the `production` environment only ====================
    // Previously trusted `repo:argamanza/wiki7:*`, which let ANY branch/PR workflow in the
    // repo assume deploy-grade credentials (the bootstrap deploy role passes the admin
    // cfn-exec role to CloudFormation). Tightened to:
    //   • ref:refs/heads/master           — the on-push deploy
    //   • environment:production          — jobs gated by the `production` environment
    //     (covers workflow_dispatch deploys from a branch; environment protection rules,
    //     if configured, then apply)
    // PR workflows no longer match — they use the read-only diff role below.
    const deployRole = new iam.Role(this, 'GitHubActionsDeployRole', {
      roleName: 'Wiki7GitHubActionsDeployRole',
      assumedBy: new iam.WebIdentityPrincipal(oidcProvider.openIdConnectProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        },
        StringLike: {
          'token.actions.githubusercontent.com:sub': [
            'repo:argamanza/wiki7:ref:refs/heads/master',
            'repo:argamanza/wiki7:environment:production',
          ],
        },
      }),
      maxSessionDuration: cdk.Duration.hours(1),
    });

    // The CDK bootstrap roles are the only permissions GH Actions needs.
    // `cdk deploy` itself assumes deploy/cfn-exec/file-publishing/image-publishing/lookup
    // roles, which carry the actual resource permissions. We don't need a separate
    // S3-sync policy because docker/assets/ ships via the CDK BucketDeployment construct
    // (compute-stack.ts) — there's no longer a workflow step that writes to S3 directly.
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeBootstrapRoles',
        effect: iam.Effect.ALLOW,
        actions: ['sts:AssumeRole'],
        resources: bootstrapRoleArns(['deploy', 'cfn-exec', 'file-publishing', 'image-publishing', 'lookup']),
      })
    );

    // === Diff role — pull requests, read-only ===============================================
    // cdk-diff.yml runs `npx cdk diff` on PR branches, i.e. it executes arbitrary
    // TypeScript from the PR with whatever credentials it holds. It only needs the
    // bootstrap `lookup` role (ReadOnlyAccess) — `cdk diff` assumes it for context
    // lookups and for reading the deployed stack templates.
    const diffRole = new iam.Role(this, 'GitHubActionsDiffRole', {
      roleName: 'Wiki7GitHubActionsDiffRole',
      assumedBy: new iam.WebIdentityPrincipal(oidcProvider.openIdConnectProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
          // pull_request workflows carry exactly this sub claim.
          'token.actions.githubusercontent.com:sub': 'repo:argamanza/wiki7:pull_request',
        },
      }),
      maxSessionDuration: cdk.Duration.hours(1),
    });
    diffRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeLookupRoleOnly',
        effect: iam.Effect.ALLOW,
        actions: ['sts:AssumeRole'],
        resources: bootstrapRoleArns(['lookup']),
      })
    );

    new cdk.CfnOutput(this, 'RoleArn', {
      value: deployRole.roleArn,
      description: 'IAM role ARN for GitHub Actions OIDC deploys',
    });
    new cdk.CfnOutput(this, 'DiffRoleArn', {
      value: diffRole.roleArn,
      description: 'IAM role ARN for read-only PR cdk-diff runs',
    });
  }
}
