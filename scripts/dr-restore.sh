#!/bin/bash
set -euo pipefail

# Wiki7 DR Restore Script (EC2 architecture — ADR-0001)
# Restores the RDS database from a snapshot (or point-in-time) and walks the
# RENAME DANCE needed to put the restored instance into service.
#
# Why a rename dance: the MediaWiki container gets MEDIAWIKI_DB_HOST baked in
# at EC2 boot from `dbInstance.dbInstanceEndpointAddress` inside CloudFormation.
# You cannot "point the stack" at an out-of-band restored instance — but the RDS
# endpoint hostname is derived from the DB instance IDENTIFIER, so renaming the
# restored instance to the original identifier makes the existing endpoint
# resolve to the restored data with zero CDK/CFN changes.
#
# This script automates the restore + verification; the cutover steps are
# printed (not executed) because they touch the live instance and deserve a
# human eyeball per step.
#
# Usage:
#   ./dr-restore.sh                               # Restore from latest automated snapshot
#   ./dr-restore.sh --snapshot SNAPSHOT_ID        # Restore from a specific snapshot
#   ./dr-restore.sh --pitr "2026-01-15T10:30:00Z" # Point-in-time recovery
#
# Prerequisites:
#   - AWS CLI configured (profile: argamanza)
#   - Run ./dr-test.sh periodically so this path is known-good before you need it.

AWS_PROFILE="${AWS_PROFILE:-argamanza}"
REGION="${AWS_REGION:-il-central-1}"
RESTORE_MODE="snapshot"
SNAPSHOT_ID=""
PITR_TIMESTAMP=""
RESTORED_SUFFIX="-restored-$(date +%Y%m%d%H%M%S)"

aws_cmd() { aws --profile "$AWS_PROFILE" --region "$REGION" "$@"; }

usage() {
  echo "Usage: $0 [--snapshot SNAPSHOT_ID] [--pitr TIMESTAMP]"
  echo ""
  echo "Options:"
  echo "  --snapshot ID    Restore from a specific RDS snapshot"
  echo "  --pitr TIMESTAMP Point-in-time recovery (ISO 8601 format)"
  echo "  --help           Show this help message"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --snapshot) SNAPSHOT_ID="$2"; RESTORE_MODE="snapshot"; shift 2 ;;
    --pitr) PITR_TIMESTAMP="$2"; RESTORE_MODE="pitr"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

echo "=== Wiki7 DR Restore ==="
echo "Profile: $AWS_PROFILE | Region: $REGION | Mode: $RESTORE_MODE"

# 1. Find the source DB instance
echo ""
echo "--- Step 1: Locating source DB instance ---"
SOURCE_DB=$(aws_cmd rds describe-db-instances \
  --query "DBInstances[?contains(DBInstanceIdentifier, 'wiki7')].DBInstanceIdentifier" \
  --output text | head -1)
[ -n "$SOURCE_DB" ] || { echo "ERROR: Could not find Wiki7 RDS instance"; exit 1; }
echo "Source DB: $SOURCE_DB"

RESTORED_DB="${SOURCE_DB}${RESTORED_SUFFIX}"
echo "Restored DB: $RESTORED_DB"

SUBNET_GROUP=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$SOURCE_DB" \
  --query "DBInstances[0].DBSubnetGroup.DBSubnetGroupName" --output text)
SECURITY_GROUPS=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$SOURCE_DB" \
  --query "DBInstances[0].VpcSecurityGroups[*].VpcSecurityGroupId" --output text)
INSTANCE_CLASS=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$SOURCE_DB" \
  --query "DBInstances[0].DBInstanceClass" --output text)

# 2. Restore
echo ""
echo "--- Step 2: Restoring database ---"
if [ "$RESTORE_MODE" = "pitr" ]; then
  echo "Restoring to point-in-time: $PITR_TIMESTAMP"
  aws_cmd rds restore-db-instance-to-point-in-time \
    --source-db-instance-identifier "$SOURCE_DB" \
    --target-db-instance-identifier "$RESTORED_DB" \
    --restore-time "$PITR_TIMESTAMP" \
    --db-subnet-group-name "$SUBNET_GROUP" \
    --vpc-security-group-ids $SECURITY_GROUPS \
    --db-instance-class "$INSTANCE_CLASS" \
    --no-publicly-accessible > /dev/null
else
  if [ -z "$SNAPSHOT_ID" ]; then
    echo "Finding latest automated snapshot..."
    SNAPSHOT_ID=$(aws_cmd rds describe-db-snapshots \
      --db-instance-identifier "$SOURCE_DB" \
      --snapshot-type automated \
      --query "sort_by(DBSnapshots, &SnapshotCreateTime)[-1].DBSnapshotIdentifier" \
      --output text)
    if [ "$SNAPSHOT_ID" = "None" ] || [ -z "$SNAPSHOT_ID" ]; then
      echo "ERROR: No automated snapshots found"
      exit 1
    fi
  fi
  echo "Restoring from snapshot: $SNAPSHOT_ID"
  aws_cmd rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier "$RESTORED_DB" \
    --db-snapshot-identifier "$SNAPSHOT_ID" \
    --db-subnet-group-name "$SUBNET_GROUP" \
    --vpc-security-group-ids $SECURITY_GROUPS \
    --db-instance-class "$INSTANCE_CLASS" \
    --no-publicly-accessible > /dev/null
fi

# 3. Wait for the restored instance
echo ""
echo "--- Step 3: Waiting for restored instance to become available ---"
echo "This may take 5-15 minutes..."
aws_cmd rds wait db-instance-available --db-instance-identifier "$RESTORED_DB"

NEW_ENDPOINT=$(aws_cmd rds describe-db-instances \
  --db-instance-identifier "$RESTORED_DB" \
  --query "DBInstances[0].Endpoint.Address" --output text)
echo "Restored instance is available at: $NEW_ENDPOINT"

# 4. Cutover instructions (manual, on purpose)
cat <<EOF

--- Step 4: Cutover (MANUAL — read each step before running it) ---

The wiki container resolves the DB by the ORIGINAL endpoint hostname, which is
derived from the identifier '$SOURCE_DB'. To put the restored data in service:

  # 4a. Verify the restored data first (same in-VPC validation dr-test.sh uses):
  #     run ./dr-test.sh logic against $NEW_ENDPOINT, or spot-check via SSM:
  #     docker exec wiki7 mysql -h $NEW_ENDPOINT ... 'SELECT COUNT(*) FROM page;'

  # 4b. Take the broken original out of the way (it is NOT deleted):
  aws rds modify-db-instance --profile $AWS_PROFILE --region $REGION \\
    --db-instance-identifier $SOURCE_DB \\
    --new-db-instance-identifier ${SOURCE_DB}-broken-$(date +%Y%m%d) --apply-immediately
  aws rds wait db-instance-available --profile $AWS_PROFILE --region $REGION \\
    --db-instance-identifier ${SOURCE_DB}-broken-$(date +%Y%m%d)

  # 4c. Rename the restored instance to the original identifier (this recreates
  #     the original endpoint hostname, so the running wiki reconnects on its own):
  aws rds modify-db-instance --profile $AWS_PROFILE --region $REGION \\
    --db-instance-identifier $RESTORED_DB \\
    --new-db-instance-identifier $SOURCE_DB --apply-immediately

  # 4d. Re-enable the protections a restored instance does NOT inherit:
  aws rds modify-db-instance --profile $AWS_PROFILE --region $REGION \\
    --db-instance-identifier $SOURCE_DB --deletion-protection --apply-immediately

  # 4e. Restart the MW container so it drops stale DB connections:
  #     (via SSM session or send-command on the wiki7 EC2)
  docker restart wiki7

  # 4f. Verify the site, then delete the broken instance WITH a final snapshot:
  curl -s 'https://wiki7.co.il/api.php?action=query&meta=siteinfo&format=json' | jq .
  aws rds delete-db-instance --profile $AWS_PROFILE --region $REGION \\
    --db-instance-identifier ${SOURCE_DB}-broken-$(date +%Y%m%d) \\
    --final-db-snapshot-identifier ${SOURCE_DB}-broken-final-$(date +%Y%m%d)

  # NOTE: CloudFormation now has benign drift (same physical identifier, new
  # resource attributes like deletionProtection state). The next 'cdk deploy'
  # reconciles it; run 'cdk diff' first and expect no replacement.

=== DR Restore: restore phase complete ===
Restored instance: $RESTORED_DB ($NEW_ENDPOINT)
The original ($SOURCE_DB) is untouched until you run the cutover above.
EOF
