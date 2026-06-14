# Iteration-cycle walk findings (modern-era)

Branch `iter-cycle-walk/modern-era`. One line per finding.
Format: `date | category | location | description | fix=…`
Categories: translation, template, spider, seo, cross, backlog-3b.

- 2026-06-14 | cross | docker/LocalSettings.php | ApprovedRevs approval-status subtitle ("this is the approved version, and it's also the latest") shown to anonymous readers on every approved mainspace page; on pending-update pages it also linked anon straight to the unapproved latest revision | fix=revoke `viewlinktolatest` from `*`, grant reviewer+sysop (mirrors runcargoqueries revocation); ships to prod via next CDK deploy
- 2026-06-14 | template | data/wiki_import/mediawiki_templates/Player_infobox.wikitext | strong-foot ("רגל חזקה") infobox row rendered TM's raw English enum (left/right/both) instead of Hebrew | fix=display-time {{#switch:}} right→ימין left→שמאל both→שתי הרגליים (reviewer-chosen); Cargo store keeps the English enum; reaches prod via pipeline template-import (import_mediawiki_templates) on next content push
