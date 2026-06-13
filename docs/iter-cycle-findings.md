# Iteration-cycle walk findings (modern-era)

Branch `iter-cycle-walk/modern-era`. One line per finding.
Format: `date | category | location | description | fix=…`
Categories: translation, template, spider, seo, cross, backlog-3b.

- 2026-06-14 | cross | docker/LocalSettings.php | ApprovedRevs approval-status subtitle ("this is the approved version, and it's also the latest") shown to anonymous readers on every approved mainspace page; on pending-update pages it also linked anon straight to the unapproved latest revision | fix=revoke `viewlinktolatest` from `*`, grant reviewer+sysop (mirrors runcargoqueries revocation); ships to prod via next CDK deploy
