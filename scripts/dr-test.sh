#!/bin/bash
set -euo pipefail

# Wiki7 DR Test Script (EC2 architecture — ADR-0001)
# Non-destructive proof that backups are restorable: restores the latest
# automated snapshot to a temporary RDS instance, then validates the data
# FROM INSIDE THE VPC by running mysql via SSM on the wiki7 EC2 instance
# (the temp instance reuses the prod DB security group, whose only ingress
# is from the MediaWiki instance SG — a laptop can never reach it, which is
# why the previous version of this script always false-failed).
#
# Mirrors the manual drill executed 2026-06-06 (revival-plan Phase 2 §backup
# drill) so the drill is repeatable instead of folklore.
#
# Usage: ./dr-test.sh
#
# Prerequisites:
#   - AWS CLI configured (profile: argamanza) with rds/ssm/ec2 read + rds restore perms
#   - The wiki7 EC2 instance running (validation executes there via SSM)

AWS_PROFILE="${AWS_PROFILE:-argamanza}"
REGION="${AWS_REGION:-il-central-1}"
TEMP_DB="wiki7-dr-test-$(date +%Y%m%d%H%M%S)"
CLEANUP_ON_EXIT=true

aws_cmd() { aws --profile "$AWS_PROFILE" --region "$REGION" "$@"; }

cleanup() {
  if [ "$CLEANUP_ON_EXIT" = true ]; then
    echo ""
    echo "--- Cleanup: Deleting temporary instance $TEMP_DB ---"
    aws_cmd rds delete-db-instance \
      --db-instance-identifier "$TEMP_DB" \
      --skip-final-snapshot \
      --delete-automated-backups 2>/dev/null || true
    echo "Cleanup initiated. Instance will be deleted in the background."
  fi
}
trap cleanup EXIT

echo "=== Wiki7 DR Test ==="
echo "Profile: $AWS_PROFILE | Region: $REGION"
echo ""

# 1. Find source DB + latest automated snapshot
#
# Filter exclusions:
#   - dr-test-*  : transient temp instances from a previous (or in-progress
#                  cleanup of a previous) drill run. Including them turns the
#                  text-output multi-row return into a tab-joined string that
#                  fails the next describe-db-snapshots call with
#                  "Input can't contain control characters". Discovered
#                  2026-06-12 when an orphan from a killed-mid-flight run was
#                  still in 'deleting' state during the next invocation.
#   - restore-*  : same idea for any other restore-shaped instance name.
# `awk 'NR==1'` (not `head -1`) extracts the first row even when text-output
# uses tabs as field separators on a single line.
echo "--- Step 1: Locating source DB and latest snapshot ---"
SOURCE_DB=$(aws_cmd rds describe-db-instances \
  --query "DBInstances[?contains(DBInstanceIdentifier, 'wiki7') && !contains(DBInstanceIdentifier, 'dr-test') && !contains(DBInstanceIdentifier, 'restore')].DBInstanceIdentifier" \
  --output text | tr '\t' '\n' | awk 'NF{print; exit}')
[ -n "$SOURCE_DB" ] || { echo "ERROR: Could not find Wiki7 RDS instance"; exit 1; }
echo "Source DB: $SOURCE_DB"

SNAPSHOT_ID=$(aws_cmd rds describe-db-snapshots \
  --db-instance-identifier "$SOURCE_DB" \
  --snapshot-type automated \
  --query "sort_by(DBSnapshots, &SnapshotCreateTime)[-1].DBSnapshotIdentifier" \
  --output text)
if [ "$SNAPSHOT_ID" = "None" ] || [ -z "$SNAPSHOT_ID" ]; then
  echo "ERROR: No automated snapshots found for $SOURCE_DB"
  exit 1
fi
echo "Latest snapshot: $SNAPSHOT_ID"

SUBNET_GROUP=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$SOURCE_DB" \
  --query "DBInstances[0].DBSubnetGroup.DBSubnetGroupName" --output text)
SECURITY_GROUPS=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$SOURCE_DB" \
  --query "DBInstances[0].VpcSecurityGroups[*].VpcSecurityGroupId" --output text)

# 2. Find the wiki7 EC2 instance (validation runs there) + the DB secret it can read
echo ""
echo "--- Step 2: Locating the wiki7 EC2 instance + DB secret ---"
EC2_ID=$(aws_cmd ec2 describe-instances \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=Wiki7CdkStack" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
if [ -z "$EC2_ID" ] || [ "$EC2_ID" = "None" ]; then
  echo "ERROR: Could not find a running wiki7 EC2 instance (needed to validate from inside the VPC)"
  exit 1
fi
echo "EC2 instance: $EC2_ID"

SECRET_ARN=$(aws_cmd secretsmanager list-secrets \
  --query "SecretList[?contains(Name, 'Wiki7DatabaseSecret')].ARN" \
  --output text | head -1)
[ -n "$SECRET_ARN" ] || { echo "ERROR: Could not find Wiki7DatabaseSecret"; exit 1; }

# 3. Restore snapshot to temporary instance (Graviton, matching prod's t4g class)
echo ""
echo "--- Step 3: Restoring snapshot to temporary instance ---"
echo "Temporary instance: $TEMP_DB"
aws_cmd rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$TEMP_DB" \
  --db-snapshot-identifier "$SNAPSHOT_ID" \
  --db-subnet-group-name "$SUBNET_GROUP" \
  --vpc-security-group-ids $SECURITY_GROUPS \
  --db-instance-class db.t4g.micro \
  --no-publicly-accessible > /dev/null

echo "Waiting for instance to become available (5-15 minutes)..."
aws_cmd rds wait db-instance-available --db-instance-identifier "$TEMP_DB"

TEMP_ENDPOINT=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$TEMP_DB" \
  --query "DBInstances[0].Endpoint.Address" --output text)
echo "Temporary endpoint: $TEMP_ENDPOINT"

# 4. Validate from inside the VPC via SSM.
#    The remote script fetches the DB password itself (the instance role has
#    read on Wiki7DatabaseSecret) so no secret ever appears in SSM command
#    history, and uses MYSQL_PWD so it never appears on a process command line.
#    mysql runs inside the wiki7 container, which ships mariadb-client.
echo ""
echo "--- Step 4: Validating restored data (via SSM on $EC2_ID) ---"
REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
PW=\$(aws secretsmanager get-secret-value --region $REGION --secret-id '$SECRET_ARN' --query SecretString --output text | jq -r .password)
q() { docker exec -e MYSQL_PWD="\$PW" wiki7 mysql -h '$TEMP_ENDPOINT' -u wikiuser wikidb -sse "\$1"; }
TABLES=\$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wikidb';")
echo "TABLE_COUNT=\$TABLES"
for t in page revision user text cargo_tables; do
  E=\$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='wikidb' AND table_name='\$t';")
  echo "TABLE_\${t}=\$E"
done
echo "PAGE_COUNT=\$(q 'SELECT COUNT(*) FROM page;')"
EOF
)
B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')
CMD_ID=$(aws_cmd ssm send-command \
  --document-name AWS-RunShellScript \
  --instance-ids "$EC2_ID" \
  --comment "wiki7 DR test validation against $TEMP_DB" \
  --parameters "commands=[\"echo $B64 | base64 -d | bash\"]" \
  --query "Command.CommandId" --output text)

STATUS=Pending
for i in $(seq 1 24); do
  sleep 5
  STATUS=$(aws_cmd ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$EC2_ID" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break ;; esac
done

OUTPUT=$(aws_cmd ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$EC2_ID" \
  --query StandardOutputContent --output text)
ERRORS=$(aws_cmd ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$EC2_ID" \
  --query StandardErrorContent --output text)
echo "$OUTPUT"
[ -n "$ERRORS" ] && echo "stderr: $ERRORS"

TABLE_COUNT=$(echo "$OUTPUT" | sed -n 's/^TABLE_COUNT=//p')
if [ "$STATUS" = "Success" ] && [ -n "$TABLE_COUNT" ] && [ "$TABLE_COUNT" -gt 0 ]; then
  echo ""
  echo "=== DR Test PASSED ==="
  echo "Snapshot $SNAPSHOT_ID is restorable and contains MediaWiki data ($TABLE_COUNT tables)."
  exit 0
else
  echo ""
  echo "=== DR Test FAILED (SSM status: $STATUS) ==="
  echo "Could not validate the restored database — investigate before trusting backups."
  exit 1
fi
