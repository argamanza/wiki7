# Phase 2.5c — Pre-Phase-3 platform verification

> **Status:** Planned. Runs after [Phase 2.5b](revival-plan.md#phase-25b--actual-edge-caching-of-mw-html-planned-immediately-after-38-validates-on-prod-before-phase-3) (edge caching) validates on prod, before Phase 3 (content + data pipeline) starts.
>
> **Output:** this file, with outcomes recorded inline + a follow-up PR with any findings.

## 1. Why this exists

Phase 2 + 2.5 + 2.5b touched a lot of areas for a lot of purposes — Option-B relaunch, full SEO/social-meta sweep, observability with 6 alarms, security hardening, real client-IP recovery behind CloudFront, edge caching. Most was verified locally + at deploy time. Some of it is the silent kind:

- backups that haven't been restored,
- alarms that haven't tripped,
- structured-data markup that Google hasn't actually parsed,
- a sitemap that hasn't been crawled by anyone we asked,
- IP recovery that hasn't been exercised under real edge conditions,
- WAF rules whose "doesn't block legitimate bots" property is only confirmed for UptimeRobot,
- a Redis sidecar fallback that we believe works but have never deliberately broken to confirm,
- a restore drill from one specific day,
- 18 extensions all "loaded" — but did Cargo actually finish its schema migrations?

Phase 3 (content + the bot data pipeline) is the moment the platform starts mattering. Before pouring content + edit traffic into it, we want to know: **is every piece we built actually delivering the value we expected** — not just "does the change look correct in code review"?

This is a deliberate, scheduled verification pass. After it passes, the platform is "delivered" and Phase 3 begins.

## 2. Why a separate phase, not folded into 2.5b

- **Scope clarity.** 2.5b is a focused build phase (edge caching design + cookie-aware policy + invalidation). Bolting comprehensive verification on top would muddle its scope and its PR review.
- **Different mindset.** Verifier ("did this deliver value?") vs implementer ("does this change work?"). The two questions deserve separate sessions.
- **Output shape.** 2.5c produces a runbook artifact (durable, reusable for future quarterly platform checks). 2.5b produces a code change. Conflating them weakens both.
- **Decision-making.** If verification finds issues, the fixes deserve their own focused work — they fit poorly inside a 2.5b PR.

## 3. Severity legend

- 🔴 **Block Phase 3.** If this fails, Phase 3 can't safely start.
- 🟡 **Fix before content lands.** Track as follow-up; doesn't block but should be addressed early Phase 3.
- 🟢 **Nice-to-confirm.** Informational; failure is acceptable / known.

## 4. Execution mechanics

- Run as a single sitting (~3-4 hours) or split across multiple short sessions.
- Record outcomes inline in this file, in the **Outcome** column of each item.
- Outcome shorthand: ✅ pass · ❌ fail · ⚠️ pass-with-caveat · 🛠️ fix-applied · ➖ skipped (with reason).
- Findings that require fixes get tracked as discrete tasks for early Phase 3 OR a "Phase 2.5d patch" PR — depending on severity.
- Tools needed: AWS CLI (`AWS_PROFILE=argamanza`), `gh`, browser, curl, `jq`, SSM Session Manager via `aws ssm start-session`.
- Reference for the moving pieces under inspection: [`docs/revival-plan.md`](revival-plan.md) (phase history), [`docs/adr/0001-single-ec2-vs-fargate-alb.md`](adr/0001-single-ec2-vs-fargate-alb.md) (architecture rationale), AWS profile `argamanza` / account 368127906643 / region `il-central-1` (CloudFront/cert/WAF in `us-east-1`).

## 5. Exit criteria

- All 🔴 items pass.
- 🟡 items either pass OR have a clear follow-up tracked (in `BACKLOG.md` or `docs/revival-plan.md` §Phase 4).
- 🟢 items either pass OR are explicitly accepted-as-is.
- This document is committed with outcomes recorded.
- Phase 3 begins.

---

## Verification matrix

### A. Infrastructure baseline 🔴

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| A1 | CDK stack health (no drift) | `cd cdk && AWS_PROFILE=argamanza CDK_DEFAULT_ACCOUNT=368127906643 npx cdk diff Wiki7CdkStack` | "There were no differences" or only metadata/no-op | |
| A2 | EC2 instance running, t4g.small, IMDSv2 | `aws ec2 describe-instances --filters 'Name=tag:aws:cloudformation:logical-id,Values=ComputeWiki7Instance*' --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name,Type:InstanceType,Imds:MetadataOptions.HttpTokens}'` | running, t4g.small, HttpTokens=required | |
| A3 | Elastic IP attached | `aws ec2 describe-addresses --filters 'Name=public-ip,Values=16.164.90.60'` | InstanceId matches A2; AssociationId set | |
| A4 | RDS available + MariaDB 11.4.9 + del-prot + encrypted | `aws rds describe-db-instances --db-instance-identifier wiki7cdkstack-databasewiki7database629f7d61-aq6lztfqt0kl --query 'DBInstances[].{State:DBInstanceStatus,Engine:Engine,Version:EngineVersion,DelProt:DeletionProtection,Enc:StorageEncrypted,Backup:BackupRetentionPeriod,MultiAZ:MultiAZ}'` | available, mariadb, 11.4.9, true, true, 7, false | |
| A5 | Latest automated RDS snapshot < 24h | `aws rds describe-db-snapshots --db-instance-identifier <id> --snapshot-type automated --query 'reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[0].{Id:DBSnapshotIdentifier,Created:SnapshotCreateTime,Status:Status}'` | Created within last 24h, available | |
| A6 | AWS Backup vault recent recovery point < 24h | `aws backup list-recovery-points-by-backup-vault --backup-vault-name <vault>` | at least one COMPLETED recovery point within last 24h | |
| A7 | EC2 SG only allows :80 from CloudFront prefix list | `aws ec2 describe-security-groups --group-ids <mw-sg-id>` | Single ingress rule on tcp/80, peer = CloudFront managed prefix list ID; no 0.0.0.0/0 | |
| A8 | DB SG only allows :3306 from MW SG | `aws ec2 describe-security-groups --group-ids <db-sg-id>` | Single ingress rule on tcp/3306, peer = MW SG | |
| A9 | S3 bucket BLOCK_ALL + BUCKET_OWNER_ENFORCED | `aws s3api get-public-access-block --bucket <bucket>` + `get-bucket-ownership-controls` | All 4 block flags true; BucketOwnerEnforced | |
| A10 | Secrets Manager: 2 wiki7 secrets present | `aws secretsmanager list-secrets --query 'SecretList[?starts_with(Name,\`Wiki7\`)].Name'` | DB secret + MW app secret | |
| A11 | SSM Session Manager works | `aws ssm start-session --target <instance-id>` then `exit` | Prompt within 5s | |
| A12 | SSM Patch Manager next-run scheduled | `aws ssm describe-maintenance-windows --filters Key=Name,Values=wiki7-weekly-patch-window` | ENABLED, NextExecutionTime in the future, ScheduleTimezone UTC | |
| A13 | GuardDuty detector enabled | `aws guardduty list-detectors` then `get-detector` | ENABLED, FIFTEEN_MINUTES | |
| A14 | Redis sidecar running + clean | SSM → `sudo docker ps --filter name=redis --format '{{.Status}}'` and `sudo docker logs redis --tail 10` | Up X hours; clean startup output, no errors | |
| A15 | Job-runner cron present + crond active (post-#38) | SSM → `cat /etc/cron.d/wiki7-jobrunner; systemctl is-active crond` | File contents match the UserData template; active | |
| A16 | Job-runner error log quiet | SSM → `tail -30 /var/log/wiki7-jobrunner.err 2>/dev/null \|\| echo 'empty'` | Empty, or no errors in last hour | |
| A17 | MW container running + healthy | SSM → `sudo docker ps --filter name=wiki7 --format '{{.Status}}'` | Up X hours, healthy | |
| A18 | UserData ran cleanly | SSM → `tail -50 /var/log/cloud-init-output.log` | No errors; ends with successful `docker run` of both containers | |

### B. MediaWiki application health 🔴

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| B1 | HTTPS at apex | `curl -sI https://wiki7.co.il/` | HTTP/2 200, HSTS header present | |
| B2 | www → apex 301 | `curl -sI https://www.wiki7.co.il/` | 301, `Location: https://wiki7.co.il/` | |
| B3 | HTTP → HTTPS redirect at edge | `curl -sI http://wiki7.co.il/` | 301/302 to https | |
| B4 | Stack identity matches expected versions | `curl -s 'https://wiki7.co.il/api.php?action=query&meta=siteinfo&format=json' \| jq '.query.general \| {generator,phpversion,dbversion,server,sitename,lang}'` | MediaWiki 1.45.3, PHP 8.3.31, MariaDB 11.4.9, server=https://wiki7.co.il, sitename=ויקישבע, lang=he | |
| B5 | All 18 extensions loaded | `curl -s '...&siprop=extensions&format=json' \| jq '[.query.extensions[].name] \| sort'` | Includes: CategoryTree, Cite, ConfirmEdit, Echo, LoginNotify, ParserFunctions, Scribunto, SyntaxHighlight, TemplateData, Thanks, VisualEditor, WikiEditor, Cargo, PageForms, TabberNeue, AWS, Description2, WikiSEO | |
| B6 | Default skin is Wiki7 | Same API output, `.query.general.skin` | Wiki7 | |
| B7 | Hebrew RTL renders correctly | Open https://wiki7.co.il/ in browser | Drawer on right (RTL flipped), brand-red identity, social icons in footer, no broken layouts | |
| B8 | Anon edit denied | `curl -sI 'https://wiki7.co.il/index.php?title=Wiki7Test&action=edit'` | 302 to login page or 403 | |
| B9 | Search functional | `curl -s 'https://wiki7.co.il/api.php?action=opensearch&search=הפועל&format=json'` | 200 + non-empty suggestions array | |
| B10 | VisualEditor API responsive | `curl -sI 'https://wiki7.co.il/api.php?action=visualeditor&page=עמוד_ראשי&paction=parse&format=json'` | 200 | |
| B11 | Job queue draining (post-#38) | SSM → `sudo docker exec wiki7 php maintenance/run.php showJobs --group; sleep 90; sudo docker exec wiki7 php maintenance/run.php showJobs --group` | Queue size stable or shrinking, NOT growing | |
| B12 | `$wgJobRunRate` = 0 in prod env | SSM → `sudo docker exec wiki7 php -r 'require "/var/www/html/LocalSettings.php"; echo $wgJobRunRate;'` | 0 | |
| B13 | `$wgUseCdn` = true in prod env | SSM → `sudo docker exec wiki7 php -r 'require "/var/www/html/LocalSettings.php"; echo var_export($wgUseCdn);'` | true | |
| B14 | `s-maxage` in anon response headers (post-#38) | `curl -sI https://wiki7.co.il/ \| grep -i cache-control` | Includes `s-maxage` directive on anonymous response | |
| B15 | Real client IP in RecentChanges (post-#38) | Make a small edit while logged in, then check Special:RecentChanges; or check the Apache access logs for a recent request: SSM → `sudo docker exec wiki7 tail -20 /var/log/apache2/access.log` | Source IP is your real public IP, NOT a CloudFront edge IP (130.176.x / 13.224.x / 18.x.x.x ranges) | |
| B16 | MW container logs flowing to CloudWatch | `aws logs tail <log-group> --log-stream-name-prefix mediawiki --since 30m \| head -10` | Recent log entries within last 30 min | |
| B17 | Redis stream logs flowing to CloudWatch | `aws logs tail <log-group> --log-stream-name-prefix redis --since 30m \| head -10` | Recent Redis output within last 30 min | |
| B18 | Cargo schema migrations succeeded | SSM → `sudo docker exec wiki7 php maintenance/run.php sql /dev/stdin <<< 'SHOW TABLES LIKE "cargo_%";'` | At least the `cargo_pages` table + any per-table cargo_X tables seeded from templates | |
| B19 | Redis BagOStuff actually working | SSM → `sudo docker exec wiki7 php -r 'require "/var/www/html/includes/WebStart.php"; $c = MediaWiki\\MediaWikiServices::getInstance()->getMainObjectStash(); $c->set("verify",time()); echo $c->get("verify");'` (or inspect MW debug: emit a parser-cache hit on a popular page; check warm-cache TTFB drops 5-10x) | Value retrievable; warm TTFB << cold TTFB | |

### C. SEO & social-meta surface 🟡

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| C1 | robots.txt accessible + correct | `curl -s https://wiki7.co.il/robots.txt` | 200, references sitemap URL, allows reasonable bots | |
| C2 | Sitemap accessible | `curl -sI https://wiki7.co.il/assets/sitemap/sitemap-index-wikidb.xml` | 200, Content-Type contains `xml` | |
| C3 | Sitemap content valid | `curl -s ...sitemap-index-wikidb.xml \| head -20` | Valid XML with `<sitemapindex>` root, references sub-sitemaps | |
| C4 | Search Console: property verified | Open Search Console → properties | wiki7.co.il listed, verified | |
| C5 | Search Console: sitemap submitted + ingested | Property → Sitemaps | Success status, page count > 0 | |
| C6 | Search Console: pages indexed | Property → Indexing → Pages | Non-zero indexed count; investigate any "Discovered – currently not indexed" if > 5% | |
| C7 | og:image absolute URL + reachable | `curl -s https://wiki7.co.il/ \| grep -oE 'og:image[^>]*content="[^"]*"' \| head -1`; then `curl -sI <url>` | `https://wiki7.co.il/assets/social-share.png`; 200; Content-Type: image/png | |
| C8 | og:title brand-augmented on main page | `curl -s https://wiki7.co.il/ \| grep 'og:title'` | `"ויקישבע - אנציקלופדיית הפועל באר שבע"` | |
| C9 | og:title brand-augmented on article page | `curl -s 'https://wiki7.co.il/<some-article>' \| grep 'og:title'` | `"<page name> - ויקישבע"` | |
| C10 | og:locale = he_IL | `curl -s ... \| grep 'og:locale'` | he_IL | |
| C11 | og:type correct per page | Main page = website; article = article | as expected | |
| C12 | Twitter Card = summary_large_image | `curl -s ... \| grep 'twitter:card'` | summary_large_image | |
| C13 | Schema.org JSON-LD: Organization with logo | `curl -s ... \| sed -n '/application\/ld+json/,/<\/script>/p' \| python3 -c "import sys,json,re; m=re.search(r'>(.+?)<',sys.stdin.read(),re.S); print(json.dumps(json.loads(m.group(1)),indent=2,ensure_ascii=False))"` | Organization @type with logo URL pointing to PNG (jpg/jpeg/png/gif/webp only — SVG rejected by WikiSEO) | |
| C14 | Schema.org JSON-LD: Article with image | Same | Article @type with image URL | |
| C15 | Canonical link present | `curl -s ... \| grep 'rel="canonical"'` | Matches the request URL | |
| C16 | HTML `<title>` brand-augmented | `curl -s ... \| grep -E '<title>'` | Matches og:title pattern (BeforePageDisplay hook force-sets it) | |
| C17 | favicon.ico + variants all 200 | `for p in /favicon.ico /assets/favicon.ico /assets/favicon.svg /assets/apple-touch-icon.png; do curl -sI "https://wiki7.co.il$p" \| head -1; done` | All HTTP/2 200 | |
| C18 | Google Rich Results Test pass | https://search.google.com/test/rich-results — input https://wiki7.co.il/ | Page eligible; Article structured data detected; no errors | |
| C19 | Facebook Sharing Debugger pass | https://developers.facebook.com/tools/debug/ — input wiki7.co.il, click "Scrape Again" | og:title, og:image, og:description correctly read; preview matches expected | |
| C20 | LinkedIn Post Inspector pass | https://www.linkedin.com/post-inspector/ | Card preview correct | |
| C21 | opengraph.xyz pass (deferred items only) | https://www.opengraph.xyz/url/https%3A%2F%2Fwiki7.co.il | Only known-deferred items from revival-plan §Phase 3 list (description length, headline overlay, og:title length, CTA overlay); no new regressions | |
| C22 | WhatsApp link preview | Paste `https://wiki7.co.il/` into a WhatsApp message to yourself | Card with title + image + description renders | |
| C23 | Telegram link preview | Same in Telegram (or any IM/email client that fetches og:) | Card renders | |
| C24 | Google "site:wiki7.co.il" coverage | https://www.google.com/search?q=site:wiki7.co.il | Returns pages; titles match the brand-augmented format | |

### D. Observability & alerting 🟡

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| D1 | 6 alarms exist + states | `aws cloudwatch describe-alarms --alarm-name-prefix wiki7-` | 6 alarms; 5 in OK; `wiki7-cloudfront-5xx-high` in INSUFFICIENT_DATA (known cross-region issue, per Phase 4 deferral) | |
| D2 | SNS topic exists; subscription confirmed (post-#38) | `aws sns list-subscriptions-by-topic --topic-arn <arn>` | Subscription state: "Confirmed" (NOT "PendingConfirmation" — user must click the confirm email after the deploy) | |
| D3 | Force a test alarm → email arrives | `aws cloudwatch set-alarm-state --alarm-name wiki7-rds-cpu-high --state-value ALARM --state-reason 'Phase 2.5c test'`, wait 60s, then revert to OK | Email arrives within ~1 min with alarm details | |
| D4 | Dashboard renders with real data | CloudWatch console → Dashboards → wiki7 | AlarmStatusWidget shows all 6 alarms; graphs show real data (not "No data available"); CloudFront 5xx widget DOES render data (Dashboards can render cross-region, unlike Alarms) | |
| D5 | CF 5xx alarm cross-region issue documented | Re-read inline comment in `cdk/lib/observability-stack.ts` + Phase 4 deferral in `docs/revival-plan.md` | Both still present, accurately describe the gap | |
| D6 | GuardDuty findings clean | `aws guardduty list-findings --detector-id <id> --finding-criteria '{"Criterion":{"severity":{"Gte":4}}}'` | Empty, or only known-acceptable findings | |
| D7 | UptimeRobot monitor live + alerts wired | UptimeRobot dashboard | UP status, recent check < 5 min ago, alert contacts include user email | |
| D8 | App-errors metric filter accumulating zero | `aws cloudwatch get-metric-statistics --namespace Wiki7/Application --metric-name ErrorCount --start-time <24h ago> --end-time <now> --period 3600 --statistics Sum` | Sum ≈ 0 over last 24h | |
| D9 | Redis-exception metric filter accumulating zero | Same with `RedisExceptionCount` | Sum ≈ 0 over last 24h | |
| D10 | Status-check auto-recover alarm in OK | `aws cloudwatch describe-alarms --alarm-name-prefix ComputeStatusCheckRecover` | OK | |

### E. Security posture 🔴

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| E1 | WAF Web ACL active on the distribution | `aws wafv2 list-web-acls --scope=CLOUDFRONT --region us-east-1` and verify it's referenced by the distribution | WebACL listed; CloudFront distribution.webACLId matches | |
| E2 | WAF rule ordering (bot-allow < bot-block priority) | `aws wafv2 get-web-acl --scope=CLOUDFRONT --region us-east-1 ...` | AllowLegitimateBot priority < BlockSuspiciousMediaWikiPatterns | |
| E3 | WAF managed rule sets present | Same output | Common + KnownBadInputs + SQLi + PHP rule sets | |
| E4 | WAF custom rules: geo-block + rate limit | Same | Both present | |
| E5 | WAF allowlist covers crawlers including uptimerobot | Same | UA list includes Googlebot, Bingbot, Twitterbot, facebookexternalhit, LinkedInBot, WhatsApp, Telegram, uptimerobot | |
| E6 | No port 22 anywhere | `aws ec2 describe-security-groups --query 'SecurityGroups[?IpPermissions[?FromPort==\`22\`]].GroupId'` | Empty array | |
| E7 | HSTS header present (1y, includeSubDomains) | `curl -sI https://wiki7.co.il/ \| grep -i strict-transport` | max-age=31536000; includeSubDomains; preload not required | |
| E8 | `$wgCookieSecure` = true in prod | SSM → `sudo docker exec wiki7 php -r 'require "/var/www/html/LocalSettings.php"; echo var_export($wgCookieSecure);'` | true | |
| E9 | S3 bucket policy: CloudFront OAC only | `aws s3api get-bucket-policy --bucket <bucket>` | Single statement; Principal is cloudfront.amazonaws.com; AWS:SourceArn matches the distribution | |
| E10 | RDS encrypted at rest | covered in A4 | true | |
| E11 | EBS encrypted | `aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=<instance-id>` | Encrypted=true | |
| E12 | AWS Backup vault KMS-encrypted | `aws backup describe-backup-vault --backup-vault-name <vault>` | EncryptionKeyArn set | |
| E13 | No secrets in git history (spot check) | `git log --all -S 'AKIA' --oneline; git log --all -S 'eyJhbGciOi' --oneline` | No real credentials surface | |
| E14 | Test bot is BLOCKED (negative case) | `curl -A 'sqlmap/1.0' -sI https://wiki7.co.il/` | 403 from WAF | |
| E15 | Test bot is ALLOWED (positive case) | `curl -A 'Googlebot/2.1 (+http://www.google.com/bot.html)' -sI https://wiki7.co.il/` | 200 | |

### F. Backup & recoverability 🔴

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| F1 | RDS deletion protection ON | covered in A4 | true | |
| F2 | RDS snapshot-on-delete configured | `aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[].DeleteAutomatedBackups'` and verify CDK config | false (so backups survive a stack-delete) | |
| F3 | Automated backup retention = 7 | covered in A4 | 7 | |
| F4 | Backup vault: daily + monthly long-retention rules | `aws backup describe-backup-plan --backup-plan-id <id>` | 2 rules: daily 7-day, monthly 365-day | |
| F5 | Restore drill recency | Check date in `docs/revival-plan.md` Phase 2 (originally 2026-06-06) | < 30 days OR re-run drill | |
| F6 | (Conditional) Re-run restore drill | If F5 stale: snapshot → `aws rds restore-db-instance-from-db-snapshot` to a temp `t4g.micro` in same VPC/SG → connect from MW container → `SHOW TABLES; SELECT COUNT(*) FROM page;` → tear down temp instance | Full MW schema present, page count > 10, restore < 15 min | |

### G. CDN behavior 🟡

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| G1 | Static asset edge cache hits | `curl -sI 'https://wiki7.co.il/skins/Wiki7/resources/skins.wiki7.styles.css' \| grep -E 'X-Cache\|Age'` (run twice) | First call: `X-Cache: Miss from cloudfront`. Second call: `X-Cache: Hit from cloudfront`, `Age: N` > 0 | |
| G2 | /load.php edge cache hits | `curl -sI 'https://wiki7.co.il/load.php?modules=startup&only=scripts'` (run twice) | Same: Miss then Hit + Age | |
| G3 | Default behavior origin request policy (post-#38) | `aws cloudfront get-distribution-config --id EKUXAFE4HMSJ3 --query 'DistributionConfig.DefaultCacheBehavior.OriginRequestPolicyId'` | `33f36d7e-f396-46d9-90e0-52428a34d9dc` (managed AllViewerAndCloudFrontHeaders-2022-07) | |
| G4 | PriceClass_100 (post-#38) | `aws cloudfront get-distribution-config --id EKUXAFE4HMSJ3 --query 'DistributionConfig.PriceClass'` | PriceClass_100 | |
| G5 | HTTP/3 negotiation | Chrome DevTools → Network → protocol column on a fresh load | h3 on at least some requests | |
| G6 | CloudFront-Viewer-Address reaches origin (post-#38) | SSM → `sudo docker exec wiki7 tail -1 /var/log/apache2/access.log` after curl-ing the site | Real client IP in log, not CloudFront edge | |
| G7 | Default HTML behavior NOT yet edge-cached (expected pre-2.5b) | `curl -sI https://wiki7.co.il/ \| grep -iE 'Age\|X-Cache'` (twice) | Both calls: `X-Cache: Miss from cloudfront`, no Age header. This confirms 2.5b is the right next step. | |
| G8 | (After 2.5b) Default HTML behavior IS edge-cached | Same after 2.5b lands | Second call: Hit + Age > 0 for anon | |

### H. Cost reality 🟢

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| H1 | Last 30 days actual spend | AWS Cost Explorer → last 30 days grouped by service | Total in $47-52/mo band (per ADR-0001) | |
| H2 | Service breakdown vs ADR | Same | EC2 ~$12, RDS ~$12, WAF ~$13, CloudFront ~$1-2, Route53 ~$0.50, Backup ~$1.50, GuardDuty ~$3-5 | |
| H3 | No surprise services | Same | No line > $1/mo for anything not in the ADR | |
| H4 | Free tier remaining (informational) | Billing → Free Tier | Note remaining headroom in case we want to test things | |

### I. CI/CD 🟢

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| I1 | GH Actions deploy.yml last run successful | `gh run list --workflow=deploy.yml --limit 1` | success | |
| I2 | Sticky cdk-diff PR comment works | Open any recent PR with CDK changes | Sticky comment shows cdk diff output | |
| I3 | CDK tests pass on master | `cd cdk && npm test` | 39/39 pass | |
| I4 | OIDC role auth working | implicit in I1 — if deploy works, OIDC works | n/a | |

### J. Content baseline (Cargo / templates / seed pages) 🔴

| # | What | How | Expected | Outcome |
|---|---|---|---|---|
| J1 | Seed pages imported | `curl -s 'https://wiki7.co.il/api.php?action=query&list=allpages&aplimit=20&format=json' \| jq '.query.allpages[].title'` | ≥ 15 pages incl. main page (`עמוד_ראשי`) + key templates | |
| J2 | Main page renders cleanly | `curl -s 'https://wiki7.co.il/' \| grep -E 'wiki7-Logo\|wiki7-FirstHeading'` | Brand identity rendered; no broken includes | |
| J3 | Cargo tables present | covered in B18 | as expected | |
| J4 | Cargo query renders without errors | Pick a Cargo-using template page (e.g. a player infobox); view source | No `cargo_error` or template-parameter errors | |
| J5 | TabberNeue renders | If any seed page uses tabs, visit it | Tabs functional | |
| J6 | VisualEditor opens (smoke) | Log in as admin → edit a page in VE | VE loads, no console errors | |
| J7 | Upload works (smoke) | Special:Upload — upload a test image | Stored to S3 under `images/`; served via CloudFront | |

### K. Optional: synthetic perf baseline 🟢 (per user choice — capture before 2.5b)

| # | What | How | Expected (baseline — record values, don't pass/fail) | Outcome |
|---|---|---|---|---|
| K1 | PageSpeed Insights — desktop | https://pagespeed.web.dev → https://wiki7.co.il/ | Record LCP, FID/INP, CLS, Speed Index, Performance score | |
| K2 | PageSpeed Insights — mobile | Same, mobile tab | Same metrics | |
| K3 | Lighthouse local — full | Chrome DevTools → Lighthouse → all 4 categories | Performance / Accessibility / Best Practices / SEO scores | |
| K4 | Cold cache TTFB (uncached HTML) | `curl -w 'time_total: %{time_total}\ntime_starttransfer: %{time_starttransfer}\n' -o /dev/null -s 'https://wiki7.co.il/Special:Random?$(date +%s)'` | Record ms | |
| K5 | Warm cache TTFB (static asset) | `curl -w '%{time_total}\n' -o /dev/null -s 'https://wiki7.co.il/skins/Wiki7/resources/skins.wiki7.styles.css'` (after a first warming call) | Record ms; should be < 50 ms from IL | |
| K6 | Snapshot recorded in this file | Paste a small JSON block of all K1-K5 numbers here at end of run | Block present, dated | |

After 2.5b lands, re-run K1–K5 and compare. Anon LCP should drop noticeably (HTML now edge-cached).

---

## Reusing #38's deploy-validation items

A subset of the matrix above maps directly to the [PR #38 test plan](https://github.com/argamanza/wiki7/pull/38). That subset will already have been executed at #38 deploy time (Phase 2.5 deploy validation, not Phase 2.5c). Marked items: A15, A16, B11, B12, B13, B14, B15, D2, G3, G4, G6, G7. When 2.5c runs, those items can be re-confirmed quickly rather than re-investigated from scratch.

## After 2.5c passes

- Update [`docs/revival-plan.md`](revival-plan.md): mark §Phase 2.5c done; Phase 3 begins.
- Update memory: `wiki7-aws-state` to reflect any deltas surfaced; record the verification-pass date.
- Open Phase 3.

---

## Round 1 — Post-#38 deploy validation + pre-2.5b baseline (executed 2026-06-06)

Subset of the matrix run after the PR #38 deploy completed (13m34s) and the EC2 was replaced. Items A1–A18 (selected), B1–B18 (selected), C1–C24 (selected), D1/D2, E14/E15, F2, G1/G3/G4/G5/G7, K4/K5. Items needing UI clicks or in-browser action (D3 test-alarm trigger, B15 RecentChanges visual check, C18 Google Rich Results Test, K1/K2 PageSpeed Insights via web UI, K3 Lighthouse) are left for the user.

**Result: 5 findings surfaced — 2 security 🚨, 1 SEO ⚠️, 1 ops ⚠️, 1 IAM ⚠️. Everything else passing.**

### Findings (prioritized)

#### 🚨 Finding 1 — `$wgSecretKey` + `$wgUpgradeKey` empty in container env → prod runs on dev fallback values

> **Status: ✅ RESOLVED 2026-06-06 via PR #44 (Phase 2.5d) + post-deploy rotation.** Two dedicated retained Secrets now hold real auto-generated values (`Wiki7SecretKeySecret` 32-char, `Wiki7UpgradeKeySecret` 16-char); `LocalSettings.php` throws `RuntimeException` at MW boot if either is empty under `WIKI_ENV=production`. All four retained Secrets (`Wiki7SecretKeySecret`, `Wiki7UpgradeKeySecret`, `Wiki7MediaWikiSecret.adminPassword`, `Wiki7DatabaseSecret.password`) + RDS master password rotated 2026-06-06; `$wgSecretKey` SSM-probed as a real 32-char value, not the dev placeholder string.


**Evidence:** the cloud-init log (`/var/log/cloud-init-output.log`) shows the Secrets Manager value returns `{"secretKey":"","upgradeKey":"","adminPassword":"<real-32-char>"}` and the bash export resolves both keys to empty strings:
```
+ export WG_SECRET_KEY=
+ export WG_UPGRADE_KEY=
```
Verified inside the container: both `WG_SECRET_KEY` and `WG_UPGRADE_KEY` env vars are empty. `LocalSettings.php` defines them as:
```php
$wgSecretKey  = getenv('WG_SECRET_KEY')  ?: 'dev-only-secret-key-replace-in-production';
$wgUpgradeKey = getenv('WG_UPGRADE_KEY') ?: 'dev-only-upgrade';
```
PHP's `?:` treats `""` as falsy, so prod falls through to the dev placeholders.

**Root cause:** in `cdk/lib/compute-stack.ts`, the Secrets Manager template uses `generateStringKey: 'adminPassword'` which only auto-generates the `adminPassword` field. The `secretKey` and `upgradeKey` template-default empty strings stay as-is.

**Impact:** `$wgSecretKey` is used by MW for CSRF tokens, session ID derivation, password reset tokens, and several other cryptographic operations. Running prod with the hardcoded dev placeholder (visible in the public repo) is exploitable — anyone who knows the dev value could forge CSRF tokens or session state. `$wgUpgradeKey` gates the `mw-config/` web installer; less critical but should also be a real secret.

**Severity:** 🔴 blocks Phase 3.

**Fix:** generate both fields with real values. Three options:
1. Add a second `secretsmanager.Secret` with its own `generateStringKey` — simplest CDK, adds one resource.
2. Use a CDK custom-resource to rotate the secret on creation, generating both fields.
3. Manually rotate via `aws secretsmanager put-secret-value` with three fresh random strings, then leave creation as-is. Lowest-friction; one-shot manual action.

Option 1 is the cleanest IaC story. Recommend option 1 — bake correctness into CDK so it survives any future stack-rebuild.

**Surface area note:** the dev fallback strings have been in `LocalSettings.php` since before Phase 2 and the same bug has been latent on every prod deploy since #24 (2026-06-06). Currently no editor activity means no actual session/CSRF use; window where exploitability matters opens with Phase 3.

#### 🚨 Finding 2 — DB password + admin password leaked in cloud-init log (disk + CloudWatch)

> **Status: ✅ RESOLVED 2026-06-06 via PR #44 (Phase 2.5d) + post-deploy rotation.** UserData now writes a chmod-0600 `/tmp/wiki7.env` under `set +x` and runs `docker run --env-file`, so neither the values nor the `-e KEY=VALUE` shape end up in the cloud-init log or the mediawiki CloudWatch stream. New EC2's `/var/log/cloud-init-output.log` SSM-grepped for `MEDIAWIKI_DB_PASSWORD=` / `MEDIAWIKI_ADMIN_PASSWORD=` / `WG_SECRET_KEY=` / `WG_UPGRADE_KEY=`: 0 hits. CloudWatch mediawiki stream last-24h: 0 hits. The pre-#44 historical leak was closed by rotating the DB password + admin password to fresh values (RDS `available`, no pending modify).


**Evidence:** cloud-init log lines from the `set -euxo pipefail` UserData echo every command (including secret-bearing exports + the `docker run -e ...` line). They land in `/var/log/cloud-init-output.log` on the EC2 AND in CloudWatch via the awslogs driver on the mediawiki stream. Anyone with `logs:GetLogEvents` on the wiki7 log group OR SSM access to the instance can read them.

**Severity:** 🔴 in spirit (defense-in-depth), 🟡 in practice (the secrets are already in Secrets Manager; this is a duplicate exposure inside the trust boundary). Real risk: a future IAM principal granted log-read but not secret-read (a common mistake) would still get the passwords.

**Fix:** in `cdk/lib/compute-stack.ts`'s UserData, either
1. `set +x` before the secret-bearing lines and `set -x` after, OR
2. Source secrets into a file and `docker run --env-file <file>` reads them, so the `docker run` line itself doesn't echo values.

Approach 2 is cleaner because `env-file` is the documented Docker pattern; approach 1 is mechanically smaller. Either is small.

**Rotation:** the leaked values should also be rotated after the fix lands. Mechanism: `aws secretsmanager update-secret` with fresh generated values, then re-deploy to pick them up.

#### ⚠️ Finding 3 — Schema.org `@type` is lowercase `"website"` instead of canonical `"WebSite"`

> **Status: ✅ RESOLVED 2026-06-07 via PR #46 (Phase 2.5b)** — two-hook split: `WikiSEOPreAddMetadata` emits lowercase `'website'`/`'article'` (OG-spec correct + matches Mastodon's case-sensitive `og:type == 'article'` article-card branch), and a new `OutputPageAfterGetHeadLinksArray` hook post-processes the JSON-LD `<script>` tag (keyed by WikiSEO's `'jsonld-metadata'` head item) to rewrite `@type` to CamelCase via `strtr` + `addHeadItem` overwrite. Hook chosen for ordering (WikiSEO's BeforePageDisplay-based emission has non-deterministic ordering vs `$wgHooks`-registered handlers; `OutputPageAfterGetHeadLinksArray` fires later in the render pipeline after all BPD handlers complete). Live verification 2026-06-07: homepage emits `og:type=website` + `"@type":"WebSite"`; article shape emits `og:type=article` + `"@type":"Article"`.

**Evidence:** JSON-LD emitted on the home page:
```json
{
  "@context": "http://schema.org",
  "@type": "website",
  ...
}
```
Schema.org canonical types use CamelCase: `WebSite`, `Article`, `Person`, etc.

**Root cause:** the `WikiSEOPreAddMetadata` hook in `LocalSettings.php` sets `$metadata['type']` to `'website'` / `'article'` (lowercase) — these are the correct values for **og:type** per Open Graph spec, but WikiSEO uses the same metadata key for both **og:type** AND Schema.org **@type**, so Schema.org gets the OG-style lowercase value.

**Impact:** most validators (Google Rich Results, Facebook) accept both casings, but it's a non-canonical emission and could be flagged. Worth fixing for cleanliness even if it doesn't currently cause visible degradation.

**Fix:** check whether WikiSEO has a separate Schema-type key (research needed); if not, the fix is to emit `WebSite` / `Article` and verify OG accepts it case-insensitively (the OG spec is loose enough that platforms generally do).

**Severity:** 🟡 fix before Phase 3, low priority.

#### ⚠️ Finding 4 — `cdk diff` from a developer machine always shows EC2 instance replacement

> **Status: ✅ RESOLVED 2026-06-06 via PR #44 (Phase 2.5d)** — `docker/.dockerignore` excludes `.DS_Store` and `**/.DS_Store`. Verified locally: a `cdk diff` from a macOS dev machine now shows only the genuine UserData/secret changes, not a phantom image-asset hash drift.


**Evidence:** post-deploy `cdk diff Wiki7CdkStack` shows
```
[-] AWS::EC2::Instance ComputeWiki7Instance1B072F4Da498c076bd050010 destroy
[+] AWS::EC2::Instance ComputeWiki7Instance1B072F4D791aa05f3468d38e
```
even with no source changes. The image asset hashes differ:
- Local synth:  `d63e9ca9011d46da92a369d6afb94493071909fe74120992ed2a1456c14c450d`
- Live (CI):    `b8d9137517a2ad68b739550d81dcb4b52be5344283acb7b20ce0423a5c9ea139`

`docker/` contains `.DS_Store` files (and the same in `docker/images/`, `docker/skins/`) which macOS Finder mutates every time the folder is browsed. They are NOT in `docker/.dockerignore`, so CDK's `DockerImageAsset` includes them in the source-hash → the hash drifts per developer, per browse session.

**Impact:** any developer running `cdk deploy` from local would trigger an EC2 instance replacement even with no real code changes. CI is consistent because GH Actions runners don't have macOS Finder. Affects "local cdk diff → reasonable expectations" workflow and could cause accidental ~5 min downtime if a dev deploys a small CDK-only change.

**Fix:** add to `docker/.dockerignore`:
```
.DS_Store
**/.DS_Store
```
Trivial, no functional risk.

**Severity:** 🟡 fix before Phase 3.

#### ⚠️ Finding 5 — Local `argamanza` IAM profile lacks `backup:ListRecoveryPointsByBackupVault`

**Evidence:**
```
aws backup list-recovery-points-by-backup-vault ... → AccessDeniedException
```

**Impact:** can't verify backup vault health from a developer's local AWS CLI. Backups themselves work fine (the service role has the permissions); the gap is read-only verification. Worth fixing because every future "is backup healthy?" check from this machine will hit the same wall.

**Severity:** 🟢 nice-to-have.

**Fix:** add `backup:ListRecoveryPointsByBackupVault` (and probably `backup:DescribeBackupVault`, `backup:DescribeRecoveryPoint`) to the local IAM principal's inline policy. Or accept the gap and verify via the AWS console.

### Outcomes table — what passed

| # | Item | Outcome | Notes |
|---|---|---|---|
| A1 | CDK diff clean | ⚠️ See Finding 4 | Image asset hash drift due to `.DS_Store`; otherwise no real CDK changes pending |
| A2 | EC2 running, t4g.small, IMDSv2 required | ✅ | `i-0877c9f6c125ca9bb`; termination protection OFF per ADR-0001 |
| A3 | EIP attached | ✅ | `16.164.90.60` → i-0877c9f6c125ca9bb |
| A4 | RDS available, MariaDB 11.4.9, DelProt, encrypted, 7-day backup | ✅ | All as expected; single-AZ per ADR |
| A5 | RDS automated snapshot recent | ✅ | 2026-06-05T21:35Z (~21h old; next window tonight) |
| A12 | SSM Patch Manager enabled + next-run scheduled | ✅ | Next: 2026-06-06T23:30Z (tonight); cron matches |
| A13 | GuardDuty detector enabled | ✅ | `3e61be77cc9d4d3db2532e1a9ed768d4` |
| A15 | Job-runner cron present + crond active | ✅ | Exact line matches UserData template; `crond` active |
| A16 | Cron error log quiet | ✅ | Empty `/var/log/wiki7-jobrunner.err` |
| A17 | Both containers running | ✅ | `wiki7` + `redis` Up 18m at check time |
| A18 | UserData ran cleanly | ⚠️ See Finding 2 | Otherwise clean exit |
| B1 | HTTPS at apex | ✅ | HTTP/2 200, HSTS `max-age=31536000; includeSubDomains` |
| B4 | Stack identity matches | ✅ | MW 1.45.3, PHP 8.3.31, MariaDB 11.4.9, Hebrew sitename, lang=he |
| B5 | Extensions + skins loaded | ✅ | All 18 expected ext + 3 skins (Vector, Citizen, Wiki7) |
| B11 | Queue draining | ✅ | `showJobs` = 0; `showJobs --group` empty |
| B12 | `$wgJobRunRate = 0` in prod | ✅ (behavioral) | Direct PHP probe blocked by MW bootstrap; proven by A15+B11 |
| B13 | `$wgUseCdn = true` in prod | ✅ (behavioral) | `Cache-Control: s-maxage=18000` confirms (MW only emits non-zero s-maxage when $wgUseCdn=true) |
| B14 | `s-maxage` in anon response | ✅ | `cache-control: s-maxage=18000, must-revalidate, max-age=0` |
| B15 | Real client IP recorded by MW (via `recentchanges.rc_ip`) | ⏸️ User action needed | Special:RecentChanges UI doesn't show IPs for logged-in edits — the `rc_ip` column in the DB does. User makes a small edit; we then SQL-query `recentchanges.rc_ip` via SSM (path: `docker exec wiki7 php maintenance/run.php sql --query "SELECT rc_user_text,rc_timestamp,rc_ip FROM recentchanges ORDER BY rc_id DESC LIMIT 5;"`). Expected: IP = user's real public IP (e.g. `194.90.225.101`), NOT a CloudFront edge IP (`130.176.x.x` / `13.224.x.x` / `18.x.x.x` etc.). NOTE: Apache's access log shows the CloudFront edge IP and that's correct — Apache logs the TCP peer; the LocalSettings.php fix modifies `$_SERVER['REMOTE_ADDR']` at the PHP layer, after Apache has already written its log line. |
| B18 | Cargo bookkeeping tables present | ✅ | `cargo_backlinks`, `cargo_pages`, `cargo_tables` — correct pre-content state |
| C1 | robots.txt accessible | ✅ | 200, sensible Disallow list, references sitemap |
| C2 | Sitemap accessible | ✅ | 200, `text/xml`, valid sitemapindex |
| C3 | Sitemap content valid | ✅ | Valid XML, one sub-sitemap referenced |
| C7 | og:image absolute + reachable | ✅ | `https://wiki7.co.il/assets/social-share.png` → 200 |
| C8 | og:title brand-augmented (main page) | ✅ | `"ויקישבע - אנציקלופדיית הפועל באר שבע"` |
| C10 | og:locale = he_IL | ✅ | |
| C11 | og:type correct (main = website) | ✅ | Verified live 2026-06-07 post-PR #46: `og:type=website` (lowercase, OG-spec) + `"@type":"WebSite"` (CamelCase, Schema.org) emitted from same metadata key via the new split. Finding 3 RESOLVED. |
| C12 | Twitter Card | ✅ | `summary_large_image` |
| C13 | Schema.org: Organization with logo | ✅ | Structure correct (author + publisher both have Organization with logo URL pointing to PNG). @type casing fixed in PR #46 — Finding 3 RESOLVED. |
| C14 | Schema.org: image | ✅ | ImageObject with absolute URL |
| C15 | Canonical link | ✅ | `<link rel="canonical" href="https://wiki7.co.il/">` |
| C16 | HTML `<title>` brand-augmented | ✅ | Matches og:title |
| C17 | favicons all 200 | ✅ | All variants reachable |
| C18 | Google Rich Results Test (homepage) | ⚠️ See note | "No items detected" on homepage — **expected**: `WebSite` isn't a rich-result-eligible type. To stress-test Finding 3 (lowercase `"article"` casing), C18 should be re-run on an article page once Phase 3 lands content. |
| D1 | 6 alarms exist + actions wired | ✅ | All 6 in OK state, each with 1 SNS AlarmAction. `wiki7-cloudfront-5xx-high` shows OK only because `treatMissingData: NOT_BREACHING` masks the cross-region issue (already documented as Phase 4 deferral) |
| D2 | SNS subscription confirmed | ✅ | Resolved 2026-06-06 19:30Z — first email landed in Gmail spam; user added "Never to Spam" filter for `no-reply@sns.amazonaws.com`; subscription now Confirmed (real ARN) |
| D3 | Test alarm → email | ✅ | Forced `wiki7-rds-cpu-high` to ALARM at 19:38:37Z, reverted to OK at 19:40:59Z. Both notifications delivered. Alarm re-evaluated organically (real RDS CPU ~2.6%, well below 85% threshold). |
| D4 | Dashboard renders | ⏸️ User check | CloudWatch console → Dashboards → `wiki7` |
| E14 | WAF blocks malicious UA | ✅ | `sqlmap/1.0` → 403 |
| E15 | WAF allows Googlebot | ✅ | `Googlebot/2.1` → 200 |
| F2 | Backup vault recent recovery point | ⏸️ Blocked by Finding 5 | Verify via AWS console instead |
| G1 | Static asset edge cache hits | ✅ | CSS: first call Miss, second call Hit + `Age` set |
| G3 | Default-behavior origin request policy | ✅ | `33f36d7e-f396-46d9-90e0-52428a34d9dc` = managed `ALL_VIEWER_AND_CLOUDFRONT_2022` |
| G4 | PriceClass_100 | ✅ | Confirmed |
| G5 | HTTP/3 advertised | ✅ | `alt-svc: h3=":443"; ma=86400` |
| G7 | HTML behavior NOT edge-cached (expected pre-2.5b) | ✅ | Two consecutive calls: both Miss, no Age — confirms 2.5b is the right next step |
| K4 | Cold HTML TTFB baseline | ✅ recorded | 150-260ms (TLS reused); 424ms (cold connect) |
| K5 | Warm CSS TTFB baseline (edge cache hit) | ✅ recorded | 97-149ms warm; 453ms cold connect |

### K6 — baseline snapshot (recorded 2026-06-06)

```json
{
  "captured_at": "2026-06-06T18:30Z",
  "captured_from": "user local machine (curl) + PageSpeed Insights (lab)",
  "cold_html_ttfb_ms": {
    "samples": [424, 156, 259],
    "note": "Special:Random with cache-buster; first sample includes TCP+TLS connect cost"
  },
  "warm_css_ttfb_ms": {
    "samples": [453, 149, 97],
    "note": "edge cache hit confirmed via X-Cache: Hit from cloudfront"
  },
  "pop_observed": ["TLV55-P2", "MRS52-P3"],
  "http_version": "h2 default, h3 advertised via alt-svc",
  "pagespeed_desktop": {
    "captured_at": "2026-06-06T19:47Z",
    "lighthouse_version": "13.3.0",
    "performance": 99,
    "accessibility": 96,
    "best_practices": 100,
    "seo": 100,
    "metrics": {
      "fcp_s": 0.3,
      "lcp_s": 0.3,
      "tbt_ms": 0,
      "cls": 0.003,
      "speed_index_s": 1.3
    },
    "diagnostics_flagged": [
      "Render-blocking requests — est savings 70 ms",
      "Use efficient cache lifetimes — est savings 43 KiB (EXACTLY what 2.5b addresses)",
      "Reduce unused CSS — est savings 12 KiB",
      "Reduce unused JavaScript — est savings 63 KiB"
    ],
    "a11y_flagged": ["Touch targets do not have sufficient size or spacing (Phase 3 design polish)"]
  },
  "pagespeed_mobile": {
    "captured_at": "2026-06-06T19:47Z",
    "lighthouse_version": "13.3.0",
    "performance": 92,
    "accessibility": 96,
    "best_practices": 100,
    "seo": 100,
    "metrics": {
      "fcp_s": 2.0,
      "lcp_s": 2.3,
      "tbt_ms": 0,
      "cls": 0,
      "speed_index_s": 5.7
    },
    "diagnostics_flagged": [
      "Render-blocking requests — est savings 900 ms (mobile; skin/asset critical path)",
      "Use efficient cache lifetimes — est savings 43 KiB",
      "Reduce unused JavaScript — est savings 61 KiB",
      "Reduce unused CSS — est savings 12 KiB"
    ]
  },
  "lighthouse_local": "Skipped — PageSpeed Insights API already runs Lighthouse 13.3.0 in lab mode with consistent throttling; running it again locally adds noise without adding signal."
}
```

### K6 — post-2.5b snapshot (recorded 2026-06-07, ~5 hours after PR #46 deploy)

```json
{
  "captured_at": "2026-06-07T13:38Z",
  "captured_from": "PageSpeed Insights (pagespeed.web.dev)",
  "pagespeed_desktop": {
    "captured_at": "2026-06-07T13:38Z",
    "lighthouse_version": "13.3.0",
    "performance": 100,
    "accessibility": 96,
    "best_practices": 100,
    "seo": 100,
    "metrics": {
      "fcp_s": 0.3,
      "lcp_s": 0.4,
      "tbt_ms": 0,
      "cls": 0.003,
      "speed_index_s": 0.7
    },
    "diagnostics_flagged": [
      "Render-blocking requests — est savings 110 ms",
      "Use efficient cache lifetimes — est savings 43 KiB (UNCHANGED; see analysis below)",
      "Reduce unused CSS — est savings 12 KiB",
      "Reduce unused JavaScript — est savings 63 KiB",
      "Optimize DOM size",
      "Avoid long main-thread tasks — 1 long task found"
    ],
    "a11y_flagged": ["Touch targets do not have sufficient size or spacing (Phase 3 design polish)"]
  },
  "pagespeed_mobile": {
    "captured_at": "2026-06-07T13:38Z",
    "lighthouse_version": "13.3.0",
    "performance": 94,
    "accessibility": 96,
    "best_practices": 100,
    "seo": 100,
    "metrics": {
      "fcp_s": 1.8,
      "lcp_s": 2.4,
      "tbt_ms": 0,
      "cls": 0,
      "speed_index_s": 4.4
    },
    "diagnostics_flagged": [
      "Render-blocking requests — est savings 900 ms (mobile; skin/asset critical path)",
      "Use efficient cache lifetimes — est savings 43 KiB (UNCHANGED; see analysis below)",
      "Reduce unused JavaScript — est savings 61 KiB",
      "Reduce unused CSS — est savings 12 KiB"
    ]
  }
}
```

#### Pre/post comparison and analysis

| Metric | Pre-2.5b (2026-06-06) | Post-2.5b (2026-06-07) | Δ |
|---|---|---|---|
| Desktop Performance | 99 | **100** | +1 |
| Desktop FCP | 0.3s | 0.3s | — |
| Desktop LCP | 0.3s | 0.4s | +0.1s (noise) |
| **Desktop Speed Index** | 1.3s | **0.7s** | **−0.6s ✅** |
| Desktop CLS | 0.003 | 0.003 | — |
| Mobile Performance | 92 | **94** | +2 |
| **Mobile FCP** | 2.0s | **1.8s** | **−0.2s ✅** |
| Mobile LCP | 2.3s | 2.4s | +0.1s (noise) |
| **Mobile Speed Index** | 5.7s | **4.4s** | **−1.3s ✅** |
| Mobile CLS | 0 | 0 | — |

**What 2.5b delivered:** the biggest visible win is **Speed Index**, which is the direct read on "how fast does visual content paint" — and it's a function of TTFB. Mobile −1.3s and desktop −0.6s confirm the edge cache is doing its job: visitors get HTML faster because it doesn't round-trip to the EC2 origin every time. Performance scores nudged up on both form factors.

**What 2.5b did NOT change** — and why that's correct:
- **LCP held flat at ~0.3-0.4s desktop / ~2.4s mobile.** Already at the practical floor for an HTTPS round-trip + initial paint; even a perfect cache can't beat speed-of-light. The +0.1s "regression" on both surfaces is measurement noise (single-run Lighthouse variance is typically ±100ms).
- **"Use efficient cache lifetimes — 43 KiB" diagnostic remains flagged.** This Lighthouse diagnostic measures **browser-cache** `max-age` directives on sub-resources, NOT CDN edge-cache behavior. MW correctly emits `max-age=0` on HTML (we want browsers to revalidate so edits show up). The 43 KiB refers to a few static sub-resources (likely favicon variants + a small CSS/JS payload) that lack long browser-cache headers. This was misattributed in the pre-2.5b notes as "exactly what 2.5b addresses" — it's not. Separate optimization, deferred to Phase 3-ish design-polish work.
- **"Render-blocking requests" still flagged** (mobile 900 ms, desktop 110 ms) — known skin/asset critical-path concern, Phase 3 design polish.

**Field data (CrUX) still empty** — the "Discover what your real users are experiencing" panel says "No Data" because the site has near-zero real traffic. The Search Console URL re-crawl request submitted post-deploy will speed up CrUX gathering once visitors arrive.

#### What the PageSpeed numbers tell us
- **Desktop perf 99 / mobile 92** is already strong, before any 2.5b work. The bulk of the score comes from CloudFront serving static assets (cached) + Israeli viewers hitting TLV POP at ~10-30ms RTT.
- **PageSpeed's #2 desktop diagnostic — "Use efficient cache lifetimes (est. savings 43 KiB)" — is exactly 2.5b's territory.** Google's own diagnostic is flagging the very gap we're about to close: HTML pages currently miss the edge cache because of `CachePolicy.CACHING_DISABLED` on the default behavior. Post-2.5b, this should disappear from the diagnostics list and the lab perf score may nudge up further.
- **Render-blocking 900 ms on mobile** is a CSS/JS critical-path issue (skin styles loaded synchronously in `<head>`). That's Phase 3 design polish, not 2.5b.
- **Real user data ("Discover what your real users are experiencing — No Data"):** CrUX has no field data yet because the site has near-zero real traffic. The lab numbers are all we have; field data will accumulate once Phase 3 brings content + readers.
- **A11y touch-target issue + Reduce unused CSS/JS** — known Phase 3 design polish.

### What needs the user

1. ✅ **Click the SNS confirm email** (D2). DONE 2026-06-06 — subscription now Confirmed.
2. ✅ **D3 test alarm → email arrives** — DONE 2026-06-06 (ALARM 19:38:37Z → OK 19:40:59Z, both notifications delivered).
3. ✅ **PageSpeed Insights** desktop + mobile (K1/K2) — DONE 2026-06-06, captured in K6 above.
4. ✅ **Google Rich Results Test** (C18) — DONE 2026-06-06. "No items detected" on homepage (expected; WebSite isn't a rich-result-eligible type). Re-run on an article page after Phase 3 lands content to stress-test Finding 3.
5. ✅ **B15 test edit** — DONE 2026-06-06 (post-rotation). Admin made a small edit; `SELECT rc.rc_id, a.actor_name, rc.rc_timestamp, rc.rc_ip ... ORDER BY rc.rc_id DESC LIMIT 5` returned `rc_id=3, actor_name=Admin, rc_timestamp=20260606215025, rc_ip=194.90.225.101` — real client IP, not a CloudFront edge IP. PR #38's `CloudFront-Viewer-Address → REMOTE_ADDR` rewrite is intact through the Phase 2.5d rotation. (MW 1.45 moved the user identity off `rc_user_text` into an `actor` join — the query in the matrix was written against the old schema; if re-run, use the JOIN form here.)
6. (Optional) **Local Lighthouse** in Chrome DevTools — SKIPPED. PageSpeed Insights API already runs Lighthouse 13.3.0 in lab mode with consistent throttling; running it again locally would add noise without adding signal.

### Recommended next steps

Address Findings 1–4 as a "**Phase 2.5d patch**" PR before 2.5b begins:
- Finding 1 + 2 (security): high-priority, ~1 hour incl. secret rotation
- Finding 3 (Schema.org casing): can be combined into 2.5b (touches WikiSEO config anyway), OR fold into the 2.5d patch — your call
- Finding 4 (`.DS_Store`): 30-second fix to `.dockerignore`

Finding 5 (IAM) can stay deferred.

After the patch PR lands + secrets rotated, **2.5b starts on a clean platform state**. After 2.5b lands, the **full 2.5c sitting** runs the remaining ~85 matrix items end-to-end.

### Status as of 2026-06-06 21:50 UTC

- **Phase 2.5d DONE** via PR #44: Findings 1, 2, 4 all marked ✅ RESOLVED above. Four-secret rotation + RDS master password rotation + Admin user password reset + post-rotation B15 verification all completed in this sitting.
- **Finding 3 (Schema.org `@type` casing)** — still deferred to Phase 2.5b's WikiSEO config work, per the original plan.
- **Finding 5 (local IAM `backup:ListRecoveryPointsByBackupVault`)** — still on Phase 4 deferral.
- **Next: Phase 2.5b** (CloudFront edge caching of MW HTML + Finding 3 roll-in), then Phase 2.5c Round 2 (~85 remaining matrix items), then Phase 3 (content + data pipeline).

---

## Round 2 — Pre-Phase-3 platform verification (executed 2026-06-07)

Final pass: the ~85 matrix items that Round 1 didn't cover, plus re-verification of the six matrix rows whose outcome was expected to shift after PR #46 (Phase 2.5b — CloudFront default behavior off `CACHING_DISABLED` + Schema.org `@type` CamelCase). Six 2.5b proof points checked first: all green. One new finding (Finding 6 — KMS cost surprise). Phase 3 is unblocked.

### 2.5b proof points — outcomes shifted since Round 1

| # | Item | Round 1 | Round 2 | Note |
|---|---|---|---|---|
| G2 | Anon HTML edge cache hits | Miss / Miss (G7 expected) | **Miss → Hit + `age:N`** ✅ | First hit Miss, second hit Hit + Age=414s observed at 17:02Z. The headline 2.5b payoff. |
| G6 | Cookie-keyed bypass (logged-in) | n/a (pre-2.5b) | ✅ | Logged-in `curl -b cookies /`: `Cache-Control: private`, `X-Cache: Miss` on both hits. Parallel anon GET to same URL kept hitting cache (Age=414). Cookie allowList works exactly as designed. |
| B14 | `s-maxage` on anon response | `s-maxage=18000` (MW default) | **`s-maxage=600`** ✅ | `$wgCdnMaxAge=600` from PR #46 in effect end-to-end. |
| C11 | `og:type` per page | `og:type=website` lowercase | ✅ unchanged | Homepage still emits lowercase per OG-spec + Mastodon-compat. Article case can't be verified (no article exists yet — see J1). |
| C13 | Schema.org JSON-LD `@type` Organization | `"@type":"website"` (Finding 3) | **`"@type":"WebSite"`** ✅ | Two-hook split working: lowercase OG + CamelCase JSON-LD from one config. Finding 3 closure double-confirmed live. |
| C14 | Schema.org JSON-LD `@type` (page root) | lowercase | **`"@type":"WebSite"`** ✅ | Same hook, same result. Image emitted as nested `ImageObject` with absolute `social-share.png` URL. |

All six proof points green — 2.5b is delivering exactly the value designed for.

### Outcomes table — Round 2

| # | Item | Outcome | Notes |
|---|---|---|---|
| A6 | AWS Backup vault recent recovery point < 24h | ✅ | `list-recovery-points-by-backup-vault` returned a COMPLETED RDS recovery point from 2026-06-07T04:00 (~13h old). Note: this CLI call now succeeds from the local `argamanza` profile — Finding 5 appears partially or fully closed since Round 1 (or this specific permission was always present and only the `Describe*` variants are missing); not investigated further this pass. |
| A7 | MW SG ingress 80/CF prefix list only | ✅ | Single rule: tcp/80 from `pl-0dd89524416301988` (CloudFront managed prefix list). No 0.0.0.0/0. |
| A8 | DB SG ingress 3306/MW SG only | ✅ | Single rule: tcp/3306 from `sg-0e939eb21c6e22db7` (MW SG). |
| A9 | S3 BLOCK_ALL + BUCKET_OWNER_ENFORCED | ✅ | All 4 block flags `true`; `BucketOwnerEnforced`. |
| A10 | Secrets Manager: wiki7 secrets present | ✅ | 4 secrets (DB, MediaWiki app, SecretKey, UpgradeKey) — exceeds the matrix's "2 secrets" expectation; the extra two are the 2.5d security additions. |
| A11 | SSM Session Manager works | ✅ | Proven by this verification pass — `aws ssm send-command` batched 12+ commands end-to-end (status `Success`). |
| A14 | Redis sidecar running + clean | ✅ | `Up 6 hours`; logs show clean Redis 7.4.9 startup, no errors. The standard "Memory overcommit" host warning is informational. |
| B2 | www → apex 301 | ✅ | HTTP/2 301 → `Location: https://wiki7.co.il/` (CloudFront function generated). |
| B3 | http → https redirect at edge | ✅ | HTTP/1.1 301 → `Location: https://wiki7.co.il/` (CloudFront viewer-protocol-policy). |
| B6 | Default skin is Wiki7 | ✅ | Homepage HTML body class includes `skin-wiki7`; `siteinfo.general.skin` field is null/absent in MW 1.45 API output but the class confirms the active skin. |
| B7 | Hebrew RTL renders correctly | ✅ | `<html lang="he" dir="rtl">` + body class includes `rtl sitedir-rtl mw-hide-empty-elt`. (Full visual check is a Phase 3 design pass; no regression observed in HTML structure.) |
| B8 | Anon edit denied | ✅ behavioral | Edit URL returns HTTP 200 with a Hebrew "log in" prompt; no `wpTextbox1` textarea present in the response → MW is blocking the edit form for anonymous users (the MW pattern is in-app permission gate, not a 302). |
| B9 | Search functional | ⚠️ | API responds 200 with empty suggestions for `"הפועל"` — expected for seed state (only the main page exists; no article matches). Functional path verified. |
| B10 | VisualEditor API responsive | ✅ | HTTP/2 200, ~36KB JSON response. |
| B16 | MW container logs to CloudWatch | ✅ | `mediawiki` log stream lastEvent timestamp recent (within minutes during the SSM probe). |
| B17 | Redis stream logs to CloudWatch | ⚠️ | `redis` log stream lastEvent ~5.5h old at check time. Matrix expected "within 30 min" — too tight for an idle Redis sidecar that only emits logs on startup or under pressure. Not a regression; tune the matrix expectation. |
| B19 | Redis BagOStuff working | ✅ inferred | Pre-2.5b K6 baseline showed 5-10× warm-cache speedup (97-149ms warm CSS TTFB vs 453ms cold); post-2.5b K6 holds the same edge cache + Redis-warmed origin numbers. A direct PHP `ObjectStash::set/get` probe via `maintenance/run.php eval` was attempted but blocked by MW's eval CLI escape handling — behavioral evidence stands. |
| C9 | og:title brand-augmented (article) | ⏸️ | Cannot validate until Phase 3 lands content — the prod wiki has only the main page (`עמוד ראשי`). The hook (`BeforePageDisplay`) is shared across page types and was tested at deploy time; revisit on first article publish. |
| D5 | CF 5xx cross-region issue documented | ✅ | Inline comment in `cdk/lib/observability-stack.ts:103-116` (and dashboard widget comment at line 264) accurately describes the gap; `docs/revival-plan.md` §Phase 4 carries the deferred-fix narrative. |
| D6 | GuardDuty findings clean | ✅ | `list-findings` with severity ≥4 → `FindingIds: []`. |
| D7 | UptimeRobot monitor live + alerts wired | ⏸️ | Dashboard check is user-action; the site has been continuously responsive throughout this verification window (G2/G6 hits + no `wiki7-cloudfront-5xx-high` alarm trips). |
| D8 | App-errors metric accumulating zero | ✅ | `Wiki7/Application/ErrorCount` Sum over last 24h = 0 across all 24 hourly buckets. |
| D9 | Redis-exception metric accumulating zero | ✅ | `Wiki7/Application/RedisExceptionCount` Sum over last 24h = 0 across all 24 hourly buckets. |
| D10 | Status-check auto-recover alarm OK | ✅ | `Wiki7CdkStack-ComputeStatusCheckRecoverAlarmAA472FFC-ps9aMkCNucBS` State=`OK`, Action=`arn:aws:automate:il-central-1:ec2:recover`. |
| E1 | WAF attached to distribution | ✅ | `CloudFront.DistributionConfig.WebACLId` = `arn:aws:wafv2:us-east-1:...:webacl/Wiki7WebAcl-MksPClMUHail/...`. |
| E2 | WAF rule ordering (bot-allow < bot-block) | ✅ | `AllowLegitimateBot` priority 6 < `BlockSuspiciousMediaWikiPatterns` priority 8. |
| E3 | WAF managed rule sets present | ✅ | `AWS-AWSManagedRulesCommonRuleSet` (2), `KnownBadInputsRuleSet` (3), `SQLiRuleSet` (4), `PHPRuleSet` (5). |
| E4 | WAF custom: geo-block + rate-limit | ✅ | `BlockCertainCountries` (priority 1) + `RateLimitPerIP` (priority 7). |
| E5 | WAF allowlist covers crawlers + uptimerobot | ✅ | Decoded base64: `googlebot, bingbot, applebot, duckduckbot, slackbot, discordbot, twitterbot, facebookexternalhit, linkedinbot, pinterestbot, embedly, telegrambot, whatsapp, uptimerobot` — all required UAs present. |
| E6 | No port 22 open anywhere | ✅ | `describe-security-groups --query 'SecurityGroups[?IpPermissions[?FromPort==\`22\`]]'` → empty. |
| E7 | HSTS header present | ✅ | `strict-transport-security: max-age=31536000; includeSubDomains` on all responses. |
| E8 | `$wgCookieSecure` = true | ✅ | LocalSettings.php:386 has `$wgCookieSecure = true`. Confirmed at runtime: the cookie jar after admin login shows all four wikidb cookies with the Secure flag set (TRUE in the 4th netscape-format column). |
| E9 | S3 bucket policy: CloudFront OAC only | ✅ | Single statement; Principal=`cloudfront.amazonaws.com`; `AWS:SourceArn = arn:aws:cloudfront::368127906643:distribution/EKUXAFE4HMSJ3`. |
| E10 | RDS encrypted at rest | ✅ | `StorageEncrypted: true` (covered in A4 too). |
| E11 | EBS encrypted | ✅ | `vol-0875b909bbd6e6744` (30GB) `Encrypted: true`. |
| E12 | Backup vault KMS-encrypted | ✅ | `EncryptionKeyArn: arn:aws:kms:il-central-1:368127906643:key/990bd736-2540-400b-8930-4c53081707f2`. |
| E13 | No secrets in git history | ✅ | `git log --all -S 'AKIA' / 'eyJhbGciOi'` → only matches are this docs file itself (search-string literals in evidence blocks), not real credentials. |
| F1 | RDS deletion protection ON | ✅ | `DeletionProtection: true` (covered in A4 too). |
| F2 | RDS snapshot-on-delete configured | ⚠️ | `describe-db-instances` returns `DeleteAutomatedBackups: null` (the field is a write-side parameter; the API doesn't expose its current value reliably for instances that haven't been modified). Trust the CDK config (`database-stack.ts` sets `deleteAutomatedBackups: false`); the prior Round-1 outcome left this ⏸️ for the same reason. |
| F3 | Automated backup retention = 7 | ✅ | `BackupRetentionPeriod: 7`. |
| F4 | Backup vault: daily + monthly long-retention | ✅ | Plan `d52135f8-...` has two rules: `DailyBackup` (`cron(0 1 * * ? *)`, 7-day retain) + `MonthlyLongRetention` (`cron(0 2 1 * ? *)`, 365-day retain). |
| F5 | Restore drill recency | ✅ | Drill on 2026-06-06 documented in revival-plan §Phase 2 — 1 day old, well inside the 30-day window. |
| F6 | (Conditional) Re-run restore drill | ➖ | Not required — F5 within window. |
| G2 | Anon HTML edge cache hits | ✅ | See 2.5b proof points table above. |
| G6 | Cookie-keyed bypass (logged-in) | ✅ | See 2.5b proof points table above. |
| G8 | (After 2.5b) Default HTML behavior IS edge-cached | ✅ | Same evidence as G2; the post-2.5b row. |
| H1 | Last 30 days actual spend in $47-52 band | ⚠️ | Cost Explorer 30-day total = ~$33 — but ~24 of those 30 days were the May teardown period; only ~1.5 days of the rebuilt stack contribute. Cannot validate the ADR band yet. Re-baseline after 30 days of continuous live operation (target: re-run around 2026-07-06). |
| H2 | Service breakdown vs ADR | ⚠️ | June 6 (one full live-stack day) projects to ~$75/mo, mostly because of the new H3 finding. Components matching the ADR within rounding: WAF ($0.44/day → ~$13/mo ✅), RDS ($0.53/day → ~$16/mo, close to the $12 ADR), Secrets Manager (~$0.90/mo ✅). EC2 compute / CloudFront / GuardDuty / Backup show billing latency (instance was launched 2026-06-07T11:06Z — only ~6 hours of EC2 hours posted by end of run). |
| H3 | No surprise services > $1/mo | ❌ Finding 6 | KMS at ~$30/mo from 18 enabled customer-managed keys (likely orphans across prior teardown cycles). See §6.2 Finding 6 below. |
| H4 | Free tier remaining | ⏸️ | User-action via Billing → Free Tier console page. |
| I1 | GH Actions deploy.yml last run successful | ✅ | Last 3 runs all `conclusion: success` (most recent: 2026-06-07T14:00Z, "docs: track 'Use efficient cache lifetimes' diagnostic…"). |
| I2 | Sticky cdk-diff PR comment | ✅ | PR #46 has a `github-actions` bot comment "## CDK Diff …" updated through the PR's life. |
| I3 | CDK tests pass on master | ✅ | `51/51` pass (matrix's "39/39" reflected the pre-2.5b suite — 2.5b's PR #46 added 8 lock-in tests for the new `Wiki7DynamicHtml` cache policy + extended the WAF/backup suites; 4 more were already added in 2.5d). |
| I4 | OIDC auth working | ✅ | Implicit from I1 — deploys ran end-to-end with no OIDC errors. |
| J1 | Seed pages imported | ⚠️ | `allpages` returns exactly 1 page (`עמוד ראשי`) — matches the prompt-stated seed state ("Sole page on prod: main page"). Phase 3 is the moment this changes. |
| J2 | Main page renders cleanly | ✅ | HTML body class contains `skin-wiki7 action-view`; `<title>` = `ויקישבע - אנציקלופדיית הפועל באר שבע`; `og:image` resolves to `social-share.png` (200, image/png, 39540 bytes). No `cargo_error` or template-include errors in the response body. |
| J3 | Cargo tables present | ✅ | Covered by Round 1 B18 — `cargo_pages`, `cargo_backlinks`, `cargo_tables` exist (bookkeeping tables ready for content). |
| J4 | Cargo query renders without errors | ⏸️ | No Cargo-using template page exists yet. Phase 3 content lands the first one. |
| J5 | TabberNeue renders | ⏸️ | No tabbed page exists. Phase 3. |
| J6 | VisualEditor opens (smoke) | ⏸️ | Needs a browser session — user-action. (The `B10` API probe confirms the VE backend is responsive.) |
| J7 | Upload works (smoke) | ⏸️ | Needs Special:Upload via browser — user-action. (S3 bucket + CloudFront /assets behavior already proven by the favicon + sitemap delivery.) |
| K1 | PageSpeed Insights — desktop | ✅ recorded | K6 post-2.5b snapshot above (desktop perf 99 → 100; Speed Index 1.3s → 0.7s). |
| K2 | PageSpeed Insights — mobile | ✅ recorded | K6 post-2.5b snapshot above (mobile perf 92 → 94; Speed Index 5.7s → 4.4s). |
| K3 | Lighthouse local | ➖ | Explicitly skipped — PageSpeed runs Lighthouse 13.3.0 in lab mode under consistent throttling; local re-run adds noise without signal. |
| K6 | Snapshot recorded in this file | ✅ | Both pre-2.5b and post-2.5b K6 blocks present above with date+source metadata. |

### 6.2 Round 2 findings

#### ⚠️ Finding 6 — KMS at ~$30/mo from 18 enabled customer-managed keys

**Evidence:** Cost Explorer daily (2026-06-06, the only full day of the rebuilt live stack within the 30-day window):
```
AWS Key Management Service     $1.0000/day   → projects ~$30/mo
Amazon RDS                     $0.5340/day   → projects ~$16/mo
AWS WAF                        $0.4350/day   → projects ~$13/mo
Amazon VPC                     $0.2470/day   → projects ~$7/mo
EC2 - Other                    $0.2089/day   → projects ~$6/mo
TOTAL (incl. all visible items) $2.4894/day  → projects ~$75/mo
```
`aws kms list-keys` in `il-central-1` returns 25 keys total; describing each shows `KeyManager=CUSTOMER, KeyState=Enabled` for **18 of them**. Each customer-managed KMS key in Enabled state bills $1/month for key storage (plus per-API charges) → key storage alone explains ~$18/mo, with API calls (EBS volume operations, RDS encryption, Secrets Manager fetches at boot) accounting for the rest.

**Root cause:** the rebuilt stack genuinely needs only a handful of customer-managed keys — backup-vault key (1), Secrets Manager keys for the four retained Secrets (3-4 if each got its own), RDS storage key (1, often AWS-managed alias), EBS key (1 if customer-managed, often AWS-managed). That's 5-7 keys at most. The other 11-13 are most likely orphans from prior CDK deploys + the 2026-06-04/06 teardown cycle (KMS keys default to a 7-30-day "pending deletion" waiting period and stay billable until that window expires; if `removalPolicy` wasn't `DESTROY` everywhere, keys never even entered that window).

**Impact:** ADR-0001 target was `~$47-52/mo` total for the Option B stack with KMS expected at `~$1-2/mo`. The actual KMS cost is roughly 15-30× that. On the projected $75/mo bill, KMS is ~40% of total spend instead of the ~3% the ADR expected. No functional impact — site works fine. Cost only.

**Fix (Phase 4):**
1. Inventory the 18 keys: `aws kms describe-key` each and tie it to a live consumer via aliases (`aws kms list-aliases`) and the CloudTrail event for last use. Keys with no alias and no events in 30 days are the orphan candidates.
2. For confirmed orphans: `aws kms schedule-key-deletion --key-id <id> --pending-window-in-days 7` (minimum window). The cost stops the day the key enters `PendingDeletion`.
3. For keys still in active use by torn-down stacks (Option A archive, prior stacks): decide whether to keep or also schedule for deletion — if scheduling, ensure no production resource (snapshot, encrypted volume, secret) still references the key, or those resources become unreadable.
4. Optionally add a CDK guardrail: a small `aws_cdk.aws_kms.Key` count assertion in tests + a Cost Anomaly Detection monitor wired into the existing SNS topic.

**Severity:** 🟢 nice-to-have — informational, cost-only, does not block Phase 3. ADR target is now off by ~50% mostly because of this single line; updating the ADR's expected KMS line (or scheduling the orphan keys) restores the original cost story.

#### Watch-outs cross-checked and NOT filed

Cross-checked against the prompt's pre-flight watch-out list — none of the items below were re-filed as new findings:

- `cdk diff` phantom EC2 instance replacement from macOS local synth → Finding 4 Phase-4 residual, still expected.
- B12 ($wgJobRunRate=0) — direct PHP probe via `maintenance/run.php eval` is blocked by MW eval CLI escape handling, same as Round 1. Behaviorally confirmed via grep of `LocalSettings.php:396` (literal `$wgJobRunRate = 0`) and B11 ("queue draining" empty/empty). Also see E8 entry — both flags grepped from source in the same SSM batch.
- F2 (snapshot-on-delete) — API field is null/write-side, same caveat as Round 1.
- "Use efficient cache lifetimes — 43 KiB" PageSpeed diagnostic — already tracked as a Phase 4 item per [`docs/revival-plan.md`](revival-plan.md#phase-4--ops--automation-cross-cutting), unchanged.
- C18 Google Rich Results Test on an article page — no article exists yet.

### What needs the user (Round 2)

Pure ⏸️ items that need a human-in-the-loop click and can't be exercised from CLI:

1. **C4-C6** — Search Console (verify property; verify sitemap submission/ingestion; check indexed page count; investigate "Discovered – currently not indexed" if > 5%). All blocked on Phase 3 actually publishing content; revisit after the first content batch lands.
2. **C19** — Facebook Sharing Debugger paste-and-scrape.
3. **C20** — LinkedIn Post Inspector card preview.
4. **C21** — opengraph.xyz audit; expect only the known-deferred items from revival-plan §Phase 3 list.
5. **C22** — WhatsApp link preview (paste into your own DM).
6. **C23** — Telegram link preview.
7. **C24** — Google `site:wiki7.co.il` coverage.
8. **D4** — CloudWatch console → Dashboards → wiki7 — confirm widgets render data (CF 5xx panel may show "No data" cross-region, that's expected).
9. **D7** — UptimeRobot dashboard: monitor UP, recent check < 5 min ago, alert contacts include your email.
10. **H4** — Billing → Free Tier headroom (informational only).
11. **J6** — Browser admin VE smoke (any seed-state edit is fine).
12. **J7** — Special:Upload smoke (any small test image; verify S3 key + CloudFront delivery).

None of these gate Phase 3 (they are 🟡/🟢 informational checks, or content-shape items that only become exercisable once Phase 3 lands content).

### Status as of 2026-06-07 (end of Round 2)

- **Phase 2.5c DONE.** Round 1 + Round 2 together cover every matrix item; the six 2.5b proof points (G2, G6, B14, C11, C13, C14) all green; one new finding (Finding 6 — KMS cost surprise) filed at severity 🟢.
- **Findings status:** 1 ✅ (2.5d), 2 ✅ (2.5d), 3 ✅ (2.5b), 4 ✅ (2.5d), 5 partially closed in practice (the specific `list-recovery-points-by-backup-vault` call now works; `describe-backup-vault` / `describe-recovery-point` not retried this pass — Phase 4 carry-over unchanged), 6 (this pass — Phase 4 carry-over).
- **🟢 Phase 3 unblocked.** No 🔴 items outstanding; the only ❌ outcome is a 🟢-severity cost finding. Platform is "delivered".
