# ADR 0001 — Single EC2 + RDS instead of Fargate + RDS + ALB

- **Status:** Accepted
- **Date:** 2026-06-06
- **Phase:** 2 (Cheap + safe relaunch)
- **Supersedes:** the "balanced cloud-native" choice locked on 2026-06-04 in [`docs/revival-plan.md`](../revival-plan.md) §5
- **Archive ref:** the original Fargate+ALB plan was implemented in full and committed at `archive/option-a-fargate-alb` (tag) — recoverable verbatim if we ever migrate back.

## Context

After Phase 1 (MediaWiki 1.45.3 + Citizen-3.17-based skin re-fork), Phase 2 needs to put the site back online. The revival plan picked a "balanced cloud-native" architecture — ECS Fargate behind an ALB, RDS MariaDB, CloudFront, WAF — sized at ~$30–45/mo.

While implementing it, the realistic monthly cost came out at **~$63/mo**, ~50% over target. The implementation was complete (tests passing, `cdk diff` clean) before we re-examined whether the architecture itself was right-sized for wiki7's actual workload.

## What wiki7 actually is

| Property | Reality |
|---|---|
| Audience | Personal Hebrew RTL fan wiki for Hapoel Beer Sheva ("ויקישבע") |
| Typical traffic | ~1 active user/day (the owner), occasional fan visits, matchday spikes ≤ 100/day |
| Content scale | Hundreds of pages, < 100 MB DB, all uploads in S3 (via AWS S3 MediaWiki ext) |
| State | Stateless web tier: cache + logs only; DB in RDS, uploads in S3 |
| Editing | Single author + the data pipeline; effectively serial |
| Cost-conscious | Yes — the entire production stack was torn down to $0.50/mo for 3 months due to cost |

## Decision

Use the **simplest architecture that satisfies {reliable, fast, modern, secure, cost-conscious}**:

- Single Graviton EC2 instance (t4g.small ARM64) in a public subnet with a static EIP, security-group-restricted to the CloudFront managed prefix list.
- The MediaWiki container is built by CDK (existing `docker/` directory, unchanged), pushed to ECR, and `docker run` is invoked from UserData on first boot. Secrets read from Secrets Manager at boot time.
- RDS MariaDB t4g.micro (Graviton) with `deletionProtection=true`, `removalPolicy=SNAPSHOT`, 7-day automated backups (the #1 lesson from the prior data loss).
- AWS Backup vault as belt-and-suspenders for the RDS (daily, 7-day retention).
- CloudFront in front (free Shield Standard DDoS, free ACM cert, edge caching for `/load.php`, `/skins/*`, `/extensions/*`).
- AWS WAF on the CloudFront distribution: Common + KnownBadInputs + SQLi + PHP managed rule sets, geo-block, rate limit, expanded legitimate-bot allow list.
- S3 storage bucket fully locked down: `BLOCK_ALL` public access, `BUCKET_OWNER_ENFORCED` ownership, CloudFront-OAC-only read path.
- Route53 A-record `ec2.wiki7.co.il → EIP` as a stable CloudFront origin hostname.
- EC2 status-check CloudWatch alarm → `ec2:recover` action for free auto-recovery on hardware failure.

No ALB. No ECS. No task definitions. No autoscaling. Plain `docker run` on an instance.

## Forces evaluated

The user's four non-negotiable properties were **reliable, very fast, modern, secure**, plus **cost-conscious**. Each property assessed for both options:

### Reliable

- **Option A (Fargate + ALB):** Fargate auto-replaces failed tasks; ALB removes failed targets in seconds; zero-downtime rolling deploys. Realistic monthly downtime: ~5 min.
- **Option B (single EC2):** EC2 status-check auto-recovery on hardware failure (~5–10 min). Deploys recreate the instance (~5 min downtime). Realistic monthly downtime: ~10 min.

**Verdict:** both >99.9%. The 5 min/mo delta is not material for a hobby wiki. **Effective tie.**

### Very fast

- **Option A:** CloudFront → ALB → Fargate → RDS.
- **Option B:** CloudFront → EC2 → RDS (one less network hop).

Cache hits — the majority of traffic — never reach origin in either case. On misses, B has ~10 ms less latency. **B slight win.**

### Modern

- **Option A** uses every "modern AWS managed-service" buzzword: Fargate, ALB, autoscaling, capacity providers.
- **Option B** uses every modern primitive that actually matters: Graviton ARM compute, container runtime, SSM Session Manager (no SSH ports open), SSM Patch Manager (hands-off OS patching), CDK IaC, CloudFront edge, encryption at rest + in flight, Secrets Manager.

"Modern" is not synonymous with "Fargate." `docker run` on a single instance, with everything else managed, is a perfectly current pattern. **Effective tie; A wins on cosmetic resume value.**

### Secure

Both options are configured identically: same CloudFront, same WAF rules, same RDS deletion-protection, same encryption, same SG-restricted-to-CloudFront ingress, same OAC-only S3 access. **Tie.**

**Smaller blast radius for B** — fewer moving parts means fewer places to misconfigure.

### Cost-conscious

- Option A: **~$63/mo** (~$760/yr)
- Option B: **~$45/mo** (~$540/yr)
- Delta: ~$18/mo / ~$220/yr / 28% saved

## Honest tradeoffs accepted in Option B

| Lost (vs A) | Mitigation / why acceptable |
|---|---|
| Multi-AZ HA — single AZ only | Single-AZ AWS SLA is 99.5% (~3.5 h/mo worst case). For a fan wiki, fine. Migrating to multi-AZ later = adding an ALB + a second instance, ~1 day of work. |
| Autoscaling 1→N | CloudFront absorbs static traffic; DB tier is identical. A traffic spike that overloads a t4g.small would need ~50× growth from current. Vertical resize (5 min downtime) handles 10× growth. |
| Zero-downtime deploys | Recreating the instance = ~5 min downtime per deploy. For a hobby wiki with weekly-at-most deploys, fine. Migration path: switch to SSM `docker pull && docker restart` for zero-downtime if it ever matters. |
| "Looks like a 2026 AWS production diagram" | Cosmetic. The architecture is provably correct for the workload; the workload doesn't justify multi-task Fargate. |

## What we deliberately did NOT compromise

- **DB data safety.** RDS managed deletion protection, snapshot-on-delete, 7-day automated backups, AWS Backup vault. The #1 lesson from the prior teardown (which lost the DB to `removalPolicy: DESTROY`).
- **HTTPS edge + DDoS protection.** CloudFront + ACM + free Shield Standard. Identical to Option A.
- **WAF coverage.** Same managed rule sets (Common, KBI, SQLi, PHP) + geo-block + rate limit + expanded bot allow list.
- **Encryption at rest.** EBS, RDS, S3 all encrypted.
- **No SSH.** SSM Session Manager is the only shell access path. No port 22 exposed.
- **S3 lockdown.** `BLOCK_ALL` + `BUCKET_OWNER_ENFORCED`; only CloudFront OAC can read.
- **IaC purity.** Everything in CDK. No manual AWS console state.
- **Secrets out of source.** All sensitive values in Secrets Manager; the EC2 IAM role grants read at boot time.

## Cost breakdown (Option B as deployed)

| Service | $/mo | Notes |
|---|---:|---|
| EC2 t4g.small on-demand (Graviton) | 12.26 | Single instance, public subnet, EIP attached |
| EBS gp3 30 GB root | 2.40 | Encrypted |
| RDS MariaDB t4g.micro | 11.83 | Graviton, deletion protection, 7-day automated backups |
| RDS storage 20 GB gp3 | 2.30 | Auto-scales to 100 GB |
| AWS Backup vault + KMS | 1.50 | Daily plan, 7-day retention |
| CloudFront | 1–2 | Mostly free tier at this traffic |
| WAF (4 managed + 4 custom + WebACL) | 13.00 | Common, KBI, SQLi, PHP managed rules |
| Route53 hosted zone | 0.50 | Existing |
| Secrets Manager (2 secrets) | 0.80 | DB creds + MW app secrets |
| S3 + Lambda + Logs + misc | 1.50 | Negligible at this scale |
| **Total** | **~$47/mo** | |

## Levers if we need to go lower

In rough order of value:

1. **Drop AWS Backup vault** (–$1.50/mo): RDS automated backups already give 7-day PITR. The vault is belt-and-suspenders.
2. **Drop SQLi + PHP managed WAF rules** (–$2/mo): keep Common + KBI as the bulk of the value.
3. **Downsize EC2 to t4g.micro** (–$6/mo): only 1 GB RAM. MediaWiki idles at ~150 MB; might OOM under load.
4. **Drop WAF entirely** (–$13/mo): rely on CloudFront's free Shield Standard DDoS + a CloudFront Function for rate limiting. Real security loss for a public site.
5. **Use Fargate Spot if/when we ever revisit A** (–$10/mo on the compute line in A).

## Migration path back to Option A

If wiki7 ever outgrows a single instance (sustained > 50 concurrent users, multi-region edits, regulatory HA requirement):

1. Check out `git show archive/option-a-fargate-alb` → the Fargate+ALB CDK is preserved verbatim.
2. Cherry-pick `compute-stack.ts` → swap for `application-stack.ts` from the archive.
3. Update `cloudfront-stack.ts` to point origin at the ALB instead of the EC2 EIP.
4. `cdk deploy` — the new ALB + Fargate stack comes up; switch DNS; tear down the EC2.
5. Total effort: ~1 day. RDS, WAF, CloudFront, DNS, OIDC, Backup, Cert, Network stacks all carry over verbatim.

The hard part of A (getting Fargate + WAF + CloudFront + RDS + OAC all working together in il-central-1) is already solved in the archive tag. The "easy migration back" is not a hypothetical benefit; it's a real, working baseline preserved by git.

## References

- [`docs/revival-plan.md`](../revival-plan.md) §2.4, §5 — the original "balanced cloud-native" choice and reasoning.
- [`PLAN.md`](../../PLAN.md) Stage 1 — the detailed task bank that drove the Option A implementation.
- Memory: `wiki7-revival-priorities`, `wiki7-aws-state` — the project context.
- Archive tag: `archive/option-a-fargate-alb` (commit `3c96252`) — the full Option A implementation, ready to cherry-pick.
- AWS pricing: il-central-1 EC2 / RDS / WAF / CloudFront, queried 2026-06-06.

## Decision log

| Date | What | Why |
|---|---|---|
| 2026-06-04 | Picked "balanced cloud-native" (Fargate + RDS + ALB) | Future-proof, IaC-pure, managed-DB safety |
| 2026-06-05 | Phase 1 merged (PR #23) | Local MW-1.45.3 ready; relaunch unblocked |
| 2026-06-06 | Implemented Option A in full, ran `cdk diff` | Surfaced honest cost ~$63/mo, ~50% over target |
| 2026-06-06 | Re-examined four properties against actual workload | A optimizes for production-SaaS shape; wiki7 is a hobby wiki |
| 2026-06-06 | Switched to Option B | Hits all four properties at ~$47/mo; ~28% cheaper; smaller blast radius |
| 2026-06-06 | Tagged `archive/option-a-fargate-alb` | Preserve verbatim for future migration if ever needed |
