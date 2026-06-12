<?php

namespace MediaWiki\Extension\Wiki7ReviewGate\Maintenance;

use Maintenance;
use MediaWiki\MediaWikiServices;
use MediaWiki\Title\Title;
use SiteStatsInit;

$IP = getenv( 'MW_INSTALL_PATH' );
if ( $IP === false ) {
	$IP = __DIR__ . '/../../../..';
}
require_once "$IP/maintenance/Maintenance.php";

/**
 * Wipe Wiki7Bot-authored content for a clean-slate restart. The safety valve
 * for the hybrid workspace policy (docs/revival-plan.md §6b) — lets the
 * operator iterate freely on prod knowing they can roll back any mess with
 * one SSM command.
 *
 * WHAT IT DELETES (--scope=all, the default)
 *   - All pages in NS_DRAFT (3000), regardless of author.
 *   - Pages in NS_MAIN + NS_TEMPLATE + NS_FILE whose *first* revision was
 *     authored by Wiki7Bot. This preserves the docker-install seed homepage
 *     and its sub-templates, because those were created by the maintenance
 *     install user, not by the bot.
 *   - All rows from every cargo_*_data table (Cargo data rows; the schemas
 *     themselves are recreated automatically by #cargo_declare on the next
 *     bot run).
 *   - All rows from approved_revs + approved_revs_files (resets all
 *     approval state — appropriate for a clean slate).
 *   - All rows from echo_event + echo_notification where event_agent_id is
 *     the bot's user_id (bot-generated review-pending notifications).
 *   - **Phase 3a R2 deep-truncation** (added 2026-06-10): every row in the
 *     audit-trail tables `archive`, `recentchanges`, `logging`, `change_tag`
 *     — and recalculates `site_stats` so `ss_total_edits` reflects the live
 *     revision count instead of the monotonic lifetime counter. The pre-R2
 *     script left ~9k rows in these tables after a bulk-import reset, which
 *     didn't actually feel like a clean slate (Special:RecentChanges still
 *     showed every delete; Special:Log carried the full audit; lifetime
 *     edit counter kept climbing). After this change, `--scope=all` produces
 *     a state as close to "fresh install" as the maintenance side can
 *     achieve without dropping the database.
 *
 *     Note: this also wipes the audit log of user-creation + group-promotion
 *     actions. The users themselves (Wiki7Bot, Admin, reviewer membership)
 *     are preserved — only the audit trail is cleared. On a dev/iteration
 *     environment this is desired; on prod, `--scope=all` carries the same
 *     destructive semantics it has always had.
 *
 * WHAT IT PRESERVES
 *   - Users + groups (Admin, Wiki7Bot, reviewer membership).
 *   - LocalSettings.php (baked into the docker image, not in the DB).
 *   - Extensions + skin (same).
 *   - Secrets in Secrets Manager (not in the DB at all).
 *   - The docker-install seed homepage עמוד ראשי + its sub-templates.
 *   - Any pages whose first revision was NOT authored by Wiki7Bot.
 *   - Cargo table SCHEMAS — only the data rows are cleared.
 *
 * SAFETY
 *   - Requires either --dry-run OR --confirm. Refuses if neither is given.
 *   - Refuses if both are given.
 *   - --dry-run prints a summary of what *would* be deleted and exits with
 *     no side-effects.
 *   - --confirm performs the actual deletion. Idempotent — safe to re-run.
 *
 * INVOCATION (after CDK deploy of this script):
 *   # dry-run
 *   aws ssm send-command --instance-ids <instance> \
 *     --document-name AWS-RunShellScript \
 *     --parameters 'commands=["docker exec wiki7 php maintenance/run.php extensions/Wiki7ReviewGate/maintenance/resetContent --dry-run"]'
 *
 *   # for real
 *   aws ssm send-command --instance-ids <instance> \
 *     --document-name AWS-RunShellScript \
 *     --parameters 'commands=["docker exec wiki7 php maintenance/run.php extensions/Wiki7ReviewGate/maintenance/resetContent --confirm"]'
 *
 * See docs/operational-bootstrap.md §7 for the full recipe.
 */
class ResetContent extends Maintenance {

	private const BOT_USERNAME = 'Wiki7Bot';
	private const NS_DRAFT = 3000;

	public function __construct() {
		parent::__construct();
		$this->addDescription(
			'Wipe Wiki7Bot-authored content (drafts + bot-imported templates + bot-imported mainspace pages); '
			. 'clear Cargo data rows, Approved Revs approvals, bot-generated Echo events. '
			. 'Preserves the docker-install seed homepage and everything outside the DB.'
		);
		$this->addOption( 'dry-run', 'Print what would be deleted without doing it.', false, false );
		$this->addOption( 'confirm', 'Required for actual deletion. Mutually exclusive with --dry-run.', false, false );
		$this->addOption( 'scope', 'Scope: "all" (default) or "drafts-only".', false, true );
		$this->requireExtension( 'Wiki7ReviewGate' );
	}

	public function execute() {
		$dryRun = $this->hasOption( 'dry-run' );
		$confirm = $this->hasOption( 'confirm' );
		$scope = $this->getOption( 'scope', 'all' );

		if ( !$dryRun && !$confirm ) {
			$this->fatalError(
				"Refusing to run: neither --dry-run nor --confirm specified.\n"
				. "Use --dry-run to preview; --confirm to actually delete."
			);
		}
		if ( $dryRun && $confirm ) {
			$this->fatalError( "Refusing to run: both --dry-run and --confirm specified. Pick one." );
		}
		if ( !in_array( $scope, [ 'all', 'drafts-only' ], true ) ) {
			$this->fatalError( "Invalid --scope: '$scope'. Use 'all' or 'drafts-only'." );
		}

		$services = MediaWikiServices::getInstance();
		$botUser = $services->getUserFactory()->newFromName( self::BOT_USERNAME );
		if ( !$botUser || !$botUser->isRegistered() ) {
			$this->fatalError(
				"Could not find user '" . self::BOT_USERNAME . "'. "
				. "Bot account must exist before reset can identify its content."
			);
		}
		$dbr = $services->getDBLoadBalancer()->getConnection( DB_REPLICA );
		$botActorId = $services->getActorStore()->findActorId( $botUser, $dbr );
		if ( $botActorId === null ) {
			$this->fatalError(
				"Could not find actor_id for '" . self::BOT_USERNAME . "'. "
				. "Has the bot ever made an edit?"
			);
		}

		$this->output( "=== Wiki7ReviewGate:resetContent ===\n" );
		$this->output( 'Mode:  ' . ( $dryRun ? 'DRY RUN (no changes)' : 'LIVE (--confirm)' ) . "\n" );
		$this->output( "Scope: $scope\n" );
		$this->output( 'Bot:   ' . self::BOT_USERNAME . " (actor_id=$botActorId, user_id={$botUser->getId()})\n\n" );

		// === Survey: pages to delete ===
		$draftPages = $this->findPagesInNamespace( self::NS_DRAFT );
		$this->output( '[pages] NS_DRAFT (' . count( $draftPages ) . " entries):\n" );
		$this->printPageSample( $draftPages );

		$botMainspacePages = [];
		$botTemplatePages = [];
		$botFilePages = [];
		if ( $scope === 'all' ) {
			$botMainspacePages = $this->findBotAuthoredPagesInNamespace( $botActorId, NS_MAIN );
			$botTemplatePages = $this->findBotAuthoredPagesInNamespace( $botActorId, NS_TEMPLATE );
			$botFilePages = $this->findBotAuthoredPagesInNamespace( $botActorId, NS_FILE );
			$this->output( '[pages] NS_MAIN bot-authored (' . count( $botMainspacePages ) . " entries):\n" );
			$this->printPageSample( $botMainspacePages );
			$this->output( '[pages] NS_TEMPLATE bot-authored (' . count( $botTemplatePages ) . " entries):\n" );
			$this->printPageSample( $botTemplatePages );
			$this->output( '[pages] NS_FILE bot-authored (' . count( $botFilePages ) . " entries):\n" );
			$this->printPageSample( $botFilePages );
		}

		$allPagesToDelete = array_merge( $draftPages, $botMainspacePages, $botTemplatePages, $botFilePages );
		$this->output( '[pages] TOTAL to delete: ' . count( $allPagesToDelete ) . "\n\n" );

		// === Survey: tables to clear ===
		$cargoTables = [];
		$cargoRowCount = 0;
		$approvedRevsRowCount = 0;
		$approvedRevsFilesRowCount = 0;
		$echoEventCount = 0;
		$echoNotificationCount = 0;
		if ( $scope === 'all' ) {
			$cargoTables = $this->listCargoDataTables( $dbr );
			foreach ( $cargoTables as $t ) {
				$cargoRowCount += (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )->from( $t )->caller( __METHOD__ )->fetchField();
			}
			$this->output( "[tables] cargo_*_data: " . count( $cargoTables ) . " tables, $cargoRowCount total rows\n" );

			if ( $dbr->tableExists( 'approved_revs', __METHOD__ ) ) {
				$approvedRevsRowCount = (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )->from( 'approved_revs' )->caller( __METHOD__ )->fetchField();
			}
			if ( $dbr->tableExists( 'approved_revs_files', __METHOD__ ) ) {
				$approvedRevsFilesRowCount = (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )->from( 'approved_revs_files' )->caller( __METHOD__ )->fetchField();
			}
			$this->output( "[tables] approved_revs: $approvedRevsRowCount rows; approved_revs_files: $approvedRevsFilesRowCount rows\n" );

			if ( $dbr->tableExists( 'echo_event', __METHOD__ ) ) {
				$echoEventCount = (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )->from( 'echo_event' )
					->where( [ 'event_agent_id' => $botUser->getId() ] )
					->caller( __METHOD__ )->fetchField();
			}
			if ( $dbr->tableExists( 'echo_notification', __METHOD__ ) ) {
				// echo_notification.notification_event joins to echo_event.event_id
				$echoNotificationCount = (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )
					->from( 'echo_notification', 'n' )
					->join( 'echo_event', 'e', 'n.notification_event = e.event_id' )
					->where( [ 'e.event_agent_id' => $botUser->getId() ] )
					->caller( __METHOD__ )->fetchField();
			}
			$this->output( "[tables] echo_event (bot-agent): $echoEventCount rows; echo_notification (joined): $echoNotificationCount rows\n" );

			// Phase 3a R2 deep-truncation survey — the audit-trail tables that
			// the pre-R2 script left untouched. Reported in dry-run so the
			// operator can see what will be wiped before confirming.
			$historyCounts = $this->countHistoryTables( $dbr );
			$this->output(
				"[tables] history (deep-truncate): "
				. "archive=" . $historyCounts['archive'] . " rows; "
				. "recentchanges=" . $historyCounts['recentchanges'] . " rows; "
				. "logging=" . $historyCounts['logging'] . " rows; "
				. "change_tag=" . $historyCounts['change_tag'] . " rows; "
				. "ss_total_edits=" . $historyCounts['ss_total_edits'] . "\n"
			);
		}

		$this->output( "\n" );

		if ( $dryRun ) {
			$this->output( "DRY RUN COMPLETE — no changes made. Re-run with --confirm to actually delete.\n" );
			return;
		}

		// === LIVE deletion ===
		$this->output( "Deleting pages...\n" );
		$deletedCount = 0;
		foreach ( $allPagesToDelete as $pageRow ) {
			if ( $this->deletePageByTitle( $pageRow['namespace'], $pageRow['title'], $botUser ) ) {
				$deletedCount++;
			}
			if ( $deletedCount > 0 && $deletedCount % 25 === 0 ) {
				$this->output( "  ... deleted $deletedCount / " . count( $allPagesToDelete ) . "\n" );
			}
		}
		$this->output( "  deleted $deletedCount pages.\n" );

		if ( $scope === 'all' ) {
			$dbw = $services->getDBLoadBalancer()->getConnection( DB_PRIMARY );
			$this->output( "Clearing Cargo data tables...\n" );
			foreach ( $cargoTables as $t ) {
				$dbw->newDeleteQueryBuilder()->deleteFrom( $t )->where( '1=1' )->caller( __METHOD__ )->execute();
			}
			$this->output( "  cleared " . count( $cargoTables ) . " cargo_*_data tables.\n" );

			// Iter-cycle 1 (2026-06-12): also clear the Cargo registry tables AND
			// drop every cargo__* data table. Without this, cargoRecreateData on
			// the next pipeline run errors with "Duplicate entry 'X' for key
			// 'cargo_tables_main_table'" — because the old template page IDs
			// are stale (templates were deleted above) but the cargo_tables rows
			// still point to them. Dropping the data tables + truncating the
			// registry forces a clean re-create on next pipeline run.
			$this->output( "Clearing Cargo registry + dropping data tables...\n" );
			$registryTables = [ 'cargo_tables', 'cargo_pages', 'cargo_backlinks' ];
			foreach ( $registryTables as $t ) {
				if ( $dbw->tableExists( $t, __METHOD__ ) ) {
					$dbw->query( "TRUNCATE TABLE `$t`", __METHOD__ );
				}
			}
			// Discover + drop every cargo__* data table (DOUBLE underscore is
			// Cargo's data-table convention; single underscore is registry).
			$res = $dbw->query(
				"SHOW TABLES LIKE 'cargo\\_\\_%'",
				__METHOD__
			);
			$dropped = 0;
			foreach ( $res as $row ) {
				$tname = array_values( (array)$row )[0];
				$dbw->query( "DROP TABLE IF EXISTS `$tname`", __METHOD__ );
				$dropped++;
			}
			$this->output( "  truncated cargo_tables + cargo_pages + cargo_backlinks; dropped $dropped cargo__* data tables.\n" );

			$this->output( "Clearing Approved Revs...\n" );
			if ( $dbw->tableExists( 'approved_revs', __METHOD__ ) ) {
				$dbw->newDeleteQueryBuilder()->deleteFrom( 'approved_revs' )->where( '1=1' )->caller( __METHOD__ )->execute();
			}
			if ( $dbw->tableExists( 'approved_revs_files', __METHOD__ ) ) {
				$dbw->newDeleteQueryBuilder()->deleteFrom( 'approved_revs_files' )->where( '1=1' )->caller( __METHOD__ )->execute();
			}
			$this->output( "  cleared approved_revs + approved_revs_files.\n" );

			$this->output( "Clearing bot-generated Echo events...\n" );
			if ( $dbw->tableExists( 'echo_notification', __METHOD__ ) && $dbw->tableExists( 'echo_event', __METHOD__ ) ) {
				// Delete notifications first (FK-style dependency).
				$dbw->query(
					'DELETE n FROM echo_notification n '
					. 'INNER JOIN echo_event e ON n.notification_event = e.event_id '
					. 'WHERE e.event_agent_id = ' . (int)$botUser->getId(),
					__METHOD__
				);
				$dbw->newDeleteQueryBuilder()
					->deleteFrom( 'echo_event' )
					->where( [ 'event_agent_id' => $botUser->getId() ] )
					->caller( __METHOD__ )->execute();
			}
			$this->output( "  cleared bot-agent rows from echo_event + echo_notification.\n" );

			// Phase 3a R2 deep-truncation — wipe audit-trail tables that the
			// page-delete cycle leaves behind, and recalculate site_stats so
			// ss_total_edits reflects the live revision count instead of the
			// monotonic lifetime counter.
			$this->output( "Deep-truncating history tables...\n" );
			$this->deepTruncateHistory( $dbw );
			$this->output( "  truncated archive + recentchanges + logging + change_tag; recalculated site_stats.\n" );
		}

		$this->output( "\nDone.\n" );
	}

	// ===========================================================================
	// Helpers
	// ===========================================================================

	/**
	 * Find every page in a given namespace, regardless of author. Used for
	 * NS_DRAFT (we wipe ALL drafts because only the bot + reviewers can edit
	 * NS_DRAFT, and a clean slate means clearing the queue entirely).
	 *
	 * @return array[] each element: [ 'id' => int, 'namespace' => int, 'title' => string ]
	 */
	private function findPagesInNamespace( int $ns ): array {
		$dbr = MediaWikiServices::getInstance()->getDBLoadBalancer()->getConnection( DB_REPLICA );
		$rows = $dbr->newSelectQueryBuilder()
			->select( [ 'page_id', 'page_namespace', 'page_title' ] )
			->from( 'page' )
			->where( [ 'page_namespace' => $ns ] )
			->caller( __METHOD__ )
			->fetchResultSet();
		$out = [];
		foreach ( $rows as $row ) {
			$out[] = [
				'id' => (int)$row->page_id,
				'namespace' => (int)$row->page_namespace,
				'title' => $row->page_title,
			];
		}
		return $out;
	}

	/**
	 * Find pages in a namespace whose *first* revision (parent_id = 0) was
	 * authored by the given actor. This preserves pages created by anyone
	 * other than the bot (the seed homepage and its sub-templates were
	 * created by the maintenance/install user during docker-entrypoint's
	 * import-pages.php run).
	 */
	private function findBotAuthoredPagesInNamespace( int $botActorId, int $ns ): array {
		$dbr = MediaWikiServices::getInstance()->getDBLoadBalancer()->getConnection( DB_REPLICA );
		$rows = $dbr->newSelectQueryBuilder()
			->select( [ 'page_id', 'page_namespace', 'page_title' ] )
			->from( 'page' )
			->join( 'revision', null, 'rev_page = page_id' )
			->where( [
				'page_namespace' => $ns,
				'rev_parent_id' => 0,
				'rev_actor' => $botActorId,
			] )
			->caller( __METHOD__ )
			->fetchResultSet();
		$out = [];
		foreach ( $rows as $row ) {
			$out[] = [
				'id' => (int)$row->page_id,
				'namespace' => (int)$row->page_namespace,
				'title' => $row->page_title,
			];
		}
		return $out;
	}

	/**
	 * Print up to 10 sample page rows; truncate the rest with a "+N more" line.
	 */
	private function printPageSample( array $pages ): void {
		$services = MediaWikiServices::getInstance();
		$nsInfo = $services->getNamespaceInfo();
		$sample = array_slice( $pages, 0, 10 );
		foreach ( $sample as $row ) {
			$nsName = $nsInfo->getCanonicalName( $row['namespace'] );
			$prefix = $nsName !== '' ? "$nsName:" : '';
			$this->output( "    {$prefix}{$row['title']}\n" );
		}
		if ( count( $pages ) > 10 ) {
			$this->output( '    ... +' . ( count( $pages ) - 10 ) . " more\n" );
		}
	}

	/**
	 * List every cargo_*_data table currently in the DB. The Cargo extension's
	 * convention: `cargo_<TemplateName>__data` for schemas declared via
	 * #cargo_declare. We don't depend on Cargo's internal API for the list —
	 * just scan the DB for tables matching the prefix.
	 */
	private function listCargoDataTables( $dbr ): array {
		// SHOW TABLES LIKE 'cargo_%' is portable across MySQL/MariaDB.
		$rows = $dbr->query( "SHOW TABLES LIKE 'cargo_%__data'", __METHOD__ );
		$out = [];
		foreach ( $rows as $row ) {
			$values = (array)$row;
			$out[] = reset( $values );
		}
		return $out;
	}

	/**
	 * Phase 3a R2: count rows in the audit-trail tables that the deep-
	 * truncation step will wipe. Used by the dry-run survey so the operator
	 * sees exactly what will be cleared before they confirm.
	 *
	 * Returns an associative array keyed by table name (or 'ss_total_edits'
	 * for the site_stats counter). Tables that don't exist on this MW
	 * version report 0 rather than crashing.
	 */
	private function countHistoryTables( $dbr ): array {
		$out = [
			'archive' => 0,
			'recentchanges' => 0,
			'logging' => 0,
			'change_tag' => 0,
			'ss_total_edits' => 0,
		];
		foreach ( [ 'archive', 'recentchanges', 'logging', 'change_tag' ] as $table ) {
			if ( $dbr->tableExists( $table, __METHOD__ ) ) {
				$out[$table] = (int)$dbr->newSelectQueryBuilder()
					->select( 'COUNT(*)' )->from( $table )
					->caller( __METHOD__ )->fetchField();
			}
		}
		if ( $dbr->tableExists( 'site_stats', __METHOD__ ) ) {
			$out['ss_total_edits'] = (int)$dbr->newSelectQueryBuilder()
				->select( 'ss_total_edits' )->from( 'site_stats' )
				->caller( __METHOD__ )->fetchField();
		}
		return $out;
	}

	/**
	 * Phase 3a R2: TRUNCATE the audit-trail tables that the per-page-delete
	 * cycle leaves behind, then recalculate site_stats so the lifetime
	 * counter ss_total_edits matches the live revision count instead of
	 * monotonically growing across iteration cycles.
	 *
	 * Uses TRUNCATE (not DELETE) because (a) we're wiping the entire table,
	 * not filtering by author, and TRUNCATE is materially faster on tables
	 * with thousands of rows; (b) the existing `--scope=all` semantics are
	 * already destructive of audit history (it deletes pages, after all);
	 * (c) for the dev/iteration environment this is the desired "fresh
	 * install" state.
	 *
	 * Each TRUNCATE is wrapped in a tableExists guard so MW versions that
	 * lack a particular table don't crash the script.
	 *
	 * The site_stats recalc uses SiteStatsInit::doAllAndCommit() — the same
	 * code path that maintenance/initSiteStats.php --update uses internally.
	 */
	private function deepTruncateHistory( $dbw ): void {
		foreach ( [ 'archive', 'recentchanges', 'logging', 'change_tag' ] as $table ) {
			if ( $dbw->tableExists( $table, __METHOD__ ) ) {
				$dbw->query( "TRUNCATE TABLE " . $dbw->tableName( $table ), __METHOD__ );
			}
		}
		// Recalculate site_stats from the live tables — same code path as
		// maintenance/initSiteStats.php --update.
		SiteStatsInit::doAllAndCommit( $dbw );
	}

	/**
	 * Delete a single page via the modern DeletePage service. Uses the bot's
	 * own user identity for the deletion log entry. Returns true on success,
	 * false on failure (logs the error but continues).
	 */
	private function deletePageByTitle( int $ns, string $dbKey, \User $performer ): bool {
		$services = MediaWikiServices::getInstance();
		$title = Title::makeTitle( $ns, $dbKey );
		if ( !$title || !$title->exists() ) {
			return false;
		}
		$page = $services->getWikiPageFactory()->newFromTitle( $title );
		$deleter = $services->getDeletePageFactory()->newDeletePage( $page, $performer );
		$status = $deleter->deleteUnsafe( 'Wiki7ReviewGate reset' );
		if ( !$status->isOK() ) {
			$this->output( '  WARN: failed to delete ' . $title->getPrefixedText() . ': ' . $status->getMessage()->text() . "\n" );
			return false;
		}
		return true;
	}
}

$maintClass = ResetContent::class;
require_once RUN_MAINTENANCE_IF_MAIN;
