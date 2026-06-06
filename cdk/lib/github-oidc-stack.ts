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

    const role = new iam.Role(this, 'GitHubActionsDeployRole', {
      roleName: 'Wiki7GitHubActionsDeployRole',
      assumedBy: new iam.WebIdentityPrincipal(oidcProvider.openIdConnectProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        },
        StringLike: {
          'token.actions.githubusercontent.com:sub': [
            'repo:argamanza/wiki7:*',
          ],
        },
      }),
      maxSessionDuration: cdk.Duration.hours(1),
    });

    const regions = ['il-central-1', 'us-east-1'];
    const cdkRolePrefixes = ['deploy', 'cfn-exec', 'file-publishing', 'image-publishing', 'lookup'];

    const cdkBootstrapRoleArns = regions.flatMap((region) =>
      cdkRolePrefixes.map(
        (prefix) => `arn:aws:iam::${account}:role/cdk-hnb659fds-${prefix}-role-${account}-${region}`
      )
    );

    // The CDK bootstrap roles are the only permissions GH Actions needs.
    // `cdk deploy` itself assumes deploy/cfn-exec/file-publishing/image-publishing/lookup
    // roles, which carry the actual resource permissions. We don't need a separate
    // S3-sync policy because docker/assets/ ships via the CDK BucketDeployment construct
    // (compute-stack.ts) — there's no longer a workflow step that writes to S3 directly.
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeBootstrapRoles',
        effect: iam.Effect.ALLOW,
        actions: ['sts:AssumeRole'],
        resources: cdkBootstrapRoleArns,
      })
    );

    new cdk.CfnOutput(this, 'RoleArn', {
      value: role.roleArn,
      description: 'IAM role ARN for GitHub Actions OIDC',
    });
  }
}
