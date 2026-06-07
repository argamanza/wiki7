<?php

namespace MediaWiki\Extension\Wiki7ReviewGate;

use Config;
use ApprovedRevs;
use MediaWiki\Permissions\PermissionManager;
use MediaWiki\SpecialPage\Hook\ChangesListSpecialPageQueryHook;
use MediaWiki\Storage\Hook\PageSaveCompleteHook;
use MediaWiki\Title\Title;
use MediaWiki\User\UserIdentity;

class Hooks implements
	PageSaveCompleteHook,
	ChangesListSpecialPageQueryHook
{
	private const NS_DRAFT = 3000;

	private PermissionManager $permissionManager;
	private Config $config;

	public function __construct( PermissionManager $permissionManager, Config $config ) {
		$this->permissionManager = $permissionManager;
		$this->config = $config;
	}

	/**
	 * BeforeCreateEchoEvent — declare our custom notification category + types.
	 *
	 * Two notification variants share one category (so the user has one
	 * preference toggle to opt-in/out of both):
	 *   - wiki7-bot-review-pending-draft  → fired on NEW page in NS_DRAFT.
	 *   - wiki7-bot-review-pending-update → fired on UPDATE that becomes an
	 *     unapproved-latest revision on an already-approved mainspace page.
	 *
	 * Not on the HookHandlers list above because Echo's hook signature is
	 * variant-by-reference (legacy style); declared statically here and wired
	 * via the legacy Hooks key in extension.json for compatibility.
	 *
	 * @param array &$notifications Echo notification type registry.
	 * @param array &$categories    Echo notification category registry.
	 */
	public static function onBeforeCreateEchoEvent( array &$notifications, array &$categories ): void {
		$category = 'wiki7-bot-review-pending';
		$categories[$category] = [
			'priority'   => 3,
			'tooltip'    => 'echo-pref-tooltip-' . $category,
			'usergroups' => [ 'reviewer', 'sysop' ],
		];

		$base = [
			'category' => $category,
			'group'    => 'positive',
			'section'  => 'alert',
			'presentation-model' => NotificationPresentationModel::class,
		];

		$notifications[$category . '-draft'] = array_merge( $base, [
			'title-message'         => 'notification-header-' . $category . '-draft',
			'compact-title-message' => 'notification-compact-header-' . $category . '-draft',
		] );
		$notifications[$category . '-update'] = array_merge( $base, [
			'title-message'         => 'notification-header-' . $category . '-update',
			'compact-title-message' => 'notification-compact-header-' . $category . '-update',
		] );
	}

	/**
	 * EchoGetDefaultNotifiedUsers — fan-out the bot-review-pending event to all
	 * users in the 'reviewer' (or 'sysop') group. Echo's `usergroups` category
	 * field only controls preference visibility, not delivery; this hook is
	 * where we tell Echo "every reviewer should get this event."
	 *
	 * Hook signature is legacy-static (Echo extension hasn't migrated this hook
	 * to an interface in MW 1.45), so this is callable from extension.json's
	 * "Hooks" entry via the HookHandlers wiring above.
	 *
	 * @param \MediaWiki\Extension\Notifications\Model\Event $event
	 * @param \MediaWiki\User\User[] &$users    Echo will notify these users (by user-id key).
	 */
	public function onEchoGetDefaultNotifiedUsers( $event, array &$users ): void {
		$type = $event->getType();
		if ( strpos( $type, 'wiki7-bot-review-pending' ) !== 0 ) {
			return;
		}
		$services = \MediaWiki\MediaWikiServices::getInstance();
		$dbr = $services->getDBLoadBalancer()->getConnection( DB_REPLICA );
		$userFactory = $services->getUserFactory();
		$uids = $dbr->newSelectQueryBuilder()
			->select( 'ug_user' )
			->distinct()
			->from( 'user_groups' )
			->where( [ 'ug_group' => [ 'reviewer', 'sysop' ] ] )
			->caller( __METHOD__ )
			->fetchFieldValues();
		foreach ( $uids as $uid ) {
			$user = $userFactory->newFromId( (int)$uid );
			if ( $user && !$user->isAnon() ) {
				$users[(int)$uid] = $user;
			}
		}
	}

	/**
	 * PageSaveComplete — the gate's main trigger.
	 *
	 * Fires AFTER every successful page save. We check:
	 *   1. The saver is in one of the "notify on" groups (default ['bot'] —
	 *      so only Wiki7Bot's writes trigger notifications; reviewers
	 *      manually editing don't generate noise).
	 *   2. The save is one of two interesting events:
	 *      (a) A new (first-revision) save in NS_DRAFT → "draft" notification.
	 *      (b) A save in mainspace that leaves the page with an approved
	 *          revision that is OLDER than the latest revision → "update"
	 *          notification.
	 *
	 * The two events route to in-wiki Echo + Telegram (if configured) in
	 * parallel; the order is "fire Echo, log Telegram failure" so a Telegram
	 * outage doesn't suppress the in-wiki notification.
	 */
	public function onPageSaveComplete( $wikiPage, $user, $summary, $flags, $revisionRecord, $editResult ): void {
		if ( $editResult->isNullEdit() ) {
			return;
		}
		$notifyGroups = $this->config->get( 'Wiki7ReviewGateNotifyOnGroups' );
		if ( !$this->userIsInAnyGroup( $user, $notifyGroups ) ) {
			return;
		}
		$title = $wikiPage->getTitle();
		$variant = $this->classifySave( $title, $revisionRecord, $editResult );
		if ( $variant === null ) {
			return;
		}
		$this->fireEchoEvent( $variant, $title, $user, $revisionRecord );
		$this->fireTelegram( $variant, $title, $user );
	}

	/**
	 * ChangesListSpecialPageQuery — filter NS_DRAFT rows out of Special:RecentChanges
	 * + watchlist for users who can't read NS_DRAFT.
	 *
	 * This is the title-leak mitigation from Phase 3.5 Open Question #2: by
	 * default the RC SQL query does not consult Lockdown's per-namespace read
	 * permissions, so draft titles + CSS classes leaked to anon visitors of
	 * Special:RecentChanges. We add an SQL condition that excludes NS_DRAFT
	 * for any user without the 'read' right on NS_DRAFT.
	 *
	 * Covers Special:RecentChanges, Special:RecentChangesLinked, Special:Watchlist
	 * (all three use this hook).
	 *
	 * @param string $name   Name of the special page firing the query.
	 * @param array  &$tables Query tables (unmodified).
	 * @param array  &$fields Query fields (unmodified).
	 * @param array  &$conds  Query WHERE conditions — we add to this.
	 * @param array  &$queryOptions
	 * @param array  &$joinConds
	 * @param mixed  $opts    FormOptions for the special page (unmodified).
	 */
	public function onChangesListSpecialPageQuery(
		$name, &$tables, &$fields, &$conds, &$queryOptions, &$joinConds, $opts
	) {
		// MW 1.45: $wgUser is a StubGlobalUser; use RequestContext directly for
		// a guaranteed-real User object.
		$user = \RequestContext::getMain()->getUser();
		if ( $this->canReadDrafts( $user ) ) {
			return;
		}
		$conds[] = 'rc_namespace != ' . self::NS_DRAFT;
	}

	/**
	 * ApiQueryBaseBeforeQuery — same gate, applied to the API surface that
	 * ChangesListSpecialPageQuery doesn't cover.
	 *
	 * Specifically: action=query&list=recentchanges and list=allpages both
	 * extend ApiQueryBase and run their SQL via this hook. The Special:* pages
	 * use a different hook path. Without this, anon API clients can still
	 * enumerate Draft: titles even though Special:RecentChanges hides them.
	 *
	 * Filters by:
	 *   - list=recentchanges -> rc_namespace != NS_DRAFT
	 *   - list=allpages      -> page_namespace != NS_DRAFT  (allpages joins on
	 *                            the `page` table directly)
	 *
	 * Other ApiQueryBase descendants (links, categorymembers, etc.) we leave
	 * alone for now; they'll need similar gating only if/when a leak surfaces.
	 *
	 * @param \ApiQueryBase $module
	 * @param array         &$tables
	 * @param array         &$fields
	 * @param array         &$conds
	 * @param array         &$queryOptions
	 * @param array         &$joinConds
	 * @param array         &$hookData
	 */
	public function onApiQueryBaseBeforeQuery(
		$module, &$tables, &$fields, &$conds, &$queryOptions, &$joinConds, &$hookData
	) {
		if ( $this->canReadDrafts( $module->getUser() ) ) {
			return;
		}
		$name = $module->getModuleName();
		if ( $name === 'recentchanges' ) {
			$conds[] = 'rc_namespace != ' . self::NS_DRAFT;
		} elseif ( $name === 'allpages' ) {
			$conds[] = 'page_namespace != ' . self::NS_DRAFT;
		}
	}

	/**
	 * SpecialPageBeforeExecute — block explicit Special:AllPages?namespace=3000
	 * (and Special:PrefixIndex, Special:Newpages) requests from non-readers.
	 *
	 * Background: Special:AllPages doesn't invoke ChangesListSpecialPageQuery or
	 * ApiQueryBaseBeforeQuery, so the title-list leaks for anon visitors who
	 * explicitly pick the Draft namespace from the dropdown. There's no clean
	 * query-level hook to filter inside the special page, so we block the page
	 * load entirely when the user asks for a namespace they can't read.
	 *
	 * @param \SpecialPage $special
	 * @param string|null  $subpage
	 */
	public function onSpecialPageBeforeExecute( $special, $subpage ) {
		$blocked = [ 'Allpages', 'Prefixindex', 'Newpages' ];
		if ( !in_array( $special->getName(), $blocked, true ) ) {
			return;
		}
		if ( $this->canReadDrafts( $special->getUser() ) ) {
			return;
		}
		$ns = (int)$special->getRequest()->getVal( 'namespace', NS_MAIN );
		if ( $ns !== self::NS_DRAFT ) {
			return;
		}
		throw new \PermissionsError( 'read', [ 'badaccess-group0' ] );
	}

	/**
	 * ApiCheckCanExecute — same gate as SpecialPageBeforeExecute, for the API.
	 *
	 * Catches action=query&list=allpages&apnamespace=3000 and equivalents
	 * (allpages, prefixsearch). The query-level filter (ApiQueryBaseBeforeQuery)
	 * only fires for ApiQueryBase descendants that go through the parent's
	 * select() method; ApiQueryAllPages builds its own SQL, so we have to
	 * gate at the request-acceptance layer instead.
	 *
	 * @param \ApiBase $module
	 * @param \User    $user
	 * @param string  &$message  Error message key to return to the API caller.
	 */
	public function onApiCheckCanExecute( $module, $user, &$message ) {
		// ApiCheckCanExecute fires only on top-level API actions (query, login,
		// edit, ...), not on sub-modules (list=allpages, prop=...). We intercept
		// at action=query and inspect the request params to detect the
		// sub-module + namespace combos that need gating.
		if ( $module->getModuleName() !== 'query' ) {
			return true;
		}
		if ( $this->canReadDrafts( $user ) ) {
			return true;
		}
		$req = $module->getRequest();
		$list = (string)$req->getVal( 'list', '' );
		if ( strpos( $list, 'allpages' ) !== false
			&& (int)$req->getVal( 'apnamespace', NS_MAIN ) === self::NS_DRAFT
		) {
			$message = 'badaccess-group0';
			return false;
		}
		if ( strpos( $list, 'prefixsearch' ) !== false
			&& (int)$req->getVal( 'psnamespace', NS_MAIN ) === self::NS_DRAFT
		) {
			$message = 'badaccess-group0';
			return false;
		}
		return true;
	}

	private function canReadDrafts( $user ): bool {
		$probe = Title::makeTitle( self::NS_DRAFT, 'X' );
		return $this->permissionManager->userCan( 'read', $user, $probe );
	}

	// -- Private helpers --------------------------------------------------

	private function userIsInAnyGroup( UserIdentity $user, array $groups ): bool {
		$userGroups = \MediaWiki\MediaWikiServices::getInstance()
			->getUserGroupManager()
			->getUserGroups( $user );
		return (bool)array_intersect( $groups, $userGroups );
	}

	/**
	 * Returns 'draft' for a new NS_DRAFT page, 'update' for a mainspace save
	 * that created an unapproved-latest situation, or null for anything else.
	 */
	private function classifySave( Title $title, $revisionRecord, $editResult ): ?string {
		$ns = $title->getNamespace();
		if ( $ns === self::NS_DRAFT ) {
			// Notify on EVERY save into NS_DRAFT — the reviewer wants to know
			// about all draft activity, both creation and bot follow-up edits
			// to a draft they haven't promoted yet.
			return 'draft';
		}
		// Mainspace + template namespace = approved-revs-gated namespaces (see
		// $egApprovedRevsEnabledNamespaces in docker/LocalSettings.php).
		if ( $ns !== NS_MAIN && $ns !== NS_TEMPLATE ) {
			return null;
		}
		// "Latest is unapproved" requires both (a) an approved revision exists,
		// AND (b) the newly-saved revision is newer than the approved one.
		// ApprovedRevs::getApprovedRevID() returns null if no approved rev.
		if ( !class_exists( ApprovedRevs::class ) ) {
			return null;
		}
		$approvedId = ApprovedRevs::getApprovedRevID( $title );
		if ( $approvedId === null ) {
			// No prior approval → no approved-rev gate active on this page yet.
			// The very first revision (which we just created) is going to be
			// approved when the reviewer first looks at it; we don't notify
			// here because the same edit that created the page would otherwise
			// double-notify the reviewer.
			return null;
		}
		if ( (int)$revisionRecord->getId() > (int)$approvedId ) {
			return 'update';
		}
		return null;
	}

	private function fireEchoEvent( string $variant, Title $title, UserIdentity $user, $revisionRecord ): void {
		if ( !class_exists( '\\EchoEvent' ) ) {
			return;
		}
		\EchoEvent::create( [
			'type'  => 'wiki7-bot-review-pending-' . $variant,
			'title' => $title,
			'agent' => $user,
			'extra' => [ 'revid' => $revisionRecord->getId() ],
		] );
	}

	/**
	 * Telegram dispatch. The token comes from an env var so it stays out of
	 * LocalSettings.php (and out of git); the chat id comes from CDK-controlled
	 * config because it's not sensitive. Both must be set or this is a no-op.
	 *
	 * Failures (HTTP error, network timeout) log to wfDebugLog but do NOT
	 * propagate — Telegram unavailability must never break a wiki edit.
	 */
	private function fireTelegram( string $variant, Title $title, UserIdentity $user ): void {
		$token  = getenv( 'WIKI7_TELEGRAM_BOT_TOKEN' ) ?: '';
		$chatId = (string)$this->config->get( 'Wiki7TelegramChatId' );
		if ( $token === '' || $chatId === '' ) {
			return;
		}

		global $wgServer, $wgScriptPath;
		$titleText = $title->getPrefixedText();
		$reviewUrl = $wgServer . $wgScriptPath . '/index.php?title=' . wfUrlencode( $title->getPrefixedDBkey() );
		$userName  = $user->getName();
		$msg = $variant === 'draft'
			? "📝 {$userName} wrote draft: {$titleText}\n→ {$reviewUrl}"
			: "🔄 {$userName} proposed update: {$titleText}\n→ {$reviewUrl}";

		$apiUrl = 'https://api.telegram.org/bot' . urlencode( $token ) . '/sendMessage';
		$payload = http_build_query( [
			'chat_id' => $chatId,
			'text'    => $msg,
			// disable_web_page_preview keeps the message terse — the review URL
			// would otherwise auto-preview the wiki page (and if the page is
			// in NS_DRAFT, Telegram's preview crawler would 403 / fall through
			// to the public page chrome anyway).
			'disable_web_page_preview' => 'true',
		] );

		$ctx = stream_context_create( [
			'http' => [
				'method'        => 'POST',
				'header'        => 'Content-Type: application/x-www-form-urlencoded',
				'content'       => $payload,
				'timeout'       => 5,
				'ignore_errors' => true,
			],
		] );
		$result = @file_get_contents( $apiUrl, false, $ctx );
		if ( $result === false ) {
			wfDebugLog( 'Wiki7ReviewGate', 'Telegram dispatch failed for ' . $titleText );
		}
	}
}
