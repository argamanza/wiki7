<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7;

use BadMethodCallException;
use MediaWiki\Cache\GenderCache;
use MediaWiki\Config\Config;
use MediaWiki\Languages\LanguageConverterFactory;
use MediaWiki\Languages\LanguageNameUtils;
use MediaWiki\MainConfigNames;
use MediaWiki\Output\OutputPage;
use MediaWiki\Permissions\PermissionManager;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentBodyContent;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentFooter;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentMainMenu;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentPageFooter;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentPageHeading;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentPageSidebar;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentPageTools;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentSiteStats;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentStickyHeader;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentTableOfContents;
use MediaWiki\Skins\Wiki7\Components\Wiki7ComponentUserInfo;
use MediaWiki\Title\Title;
use MediaWiki\User\TempUser\TempUserConfig;
use MediaWiki\User\UserFactory;
use MediaWiki\User\UserGroupManager;
use MediaWiki\User\UserIdentityLookup;
use MediaWiki\Utils\UrlUtils;
use MobileContext;
use SkinMustache;
use SkinTemplate;

/**
 * Skin subclass for Wiki7
 * @ingroup Skins
 */
class SkinWiki7 extends SkinMustache {

	private const CLIENTPREFS_THEME_MAP = [
		'auto' => 'os',
		'light' => 'day',
		'dark' => 'night'
	];

	private const DEFAULT_CLIENT_PREFS = [
		'wiki7-feature-autohide-navigation' => '1',
		'wiki7-feature-image-dimming' => '0',
		'wiki7-feature-pure-black' => '0',
		'wiki7-feature-custom-font-size' => 'standard',
		'wiki7-feature-custom-width' => 'standard',
		'wiki7-feature-performance-mode' => '1',
	];

	private const OPTIONAL_FONT_MODULES = [
		'Wiki7EnableCJKFonts' => 'skins.wiki7.styles.fonts.cjk',
		'Wiki7EnableARFonts' => 'skins.wiki7.styles.fonts.ar',
		'Wiki7EnableHEFonts' => 'skins.wiki7.styles.fonts.he',
	];

	/** For caching purposes */
	private ?array $languages = null;

	/**
	 * Merged notification data (count, alert state, link target) captured from
	 * Echo's navigation links by SkinHooks during the SkinTemplateNavigation
	 * hook. Null when the user has no notifications portlet. See
	 * {@link setNotificationData}.
	 */
	private ?array $notificationData = null;

	/**
	 * Receive merged notification data from SkinHooks::updateNotificationsMenu.
	 * Called while the SkinTemplateNavigation hook runs inside
	 * parent::getTemplateData(), so the value is ready by the time this skin
	 * assembles its own template data.
	 *
	 * @param array $data { count: int, href: string }
	 */
	public function setNotificationData( array $data ): void {
		$this->notificationData = $data;
	}

	/**
	 * Overrides template, styles and scripts module
	 *
	 * @inheritDoc
	 */
	public function __construct(
		private readonly UserFactory $userFactory,
		private readonly GenderCache $genderCache,
		private readonly UserIdentityLookup $userIdentityLookup,
		private readonly LanguageConverterFactory $languageConverterFactory,
		private readonly LanguageNameUtils $languageNameUtils,
		private readonly PermissionManager $permissionManager,
		private readonly UserGroupManager $userGroupManager,
		private readonly UrlUtils $urlUtils,
		private readonly TempUserConfig $tempUserConfig,
		// @phan-suppress-next-line PhanUndeclaredTypeParameter,PhanUndeclaredTypeProperty
		private readonly ?MobileContext $mfContext,
		array $options = []
	) {
		if ( !isset( $options['name'] ) ) {
			$options['name'] = 'wiki7';
		}

		// Add skin-specific features that only modify the $options array.
		// OutputPage modifications (HTML classes, metadata) are deferred to
		// initPage() and getHtmlElementAttributes() so that they only run
		// when Wiki7 is the active rendering skin. Without this separation,
		// Special:Preferences pollutes other skins' OutputPage when it
		// instantiates all registered skins to gather their configuration.
		$this->buildSkinFeatures( $options );
		parent::__construct( $options );
	}

	/**
	 * @inheritDoc
	 */
	public function initPage( OutputPage $out ): void {
		parent::initPage( $out );
		$this->addMetadata( $out, $this->getConfig() );
	}

	/**
	 * @inheritDoc
	 */
	public function getHtmlElementAttributes(): array {
		$attrs = parent::getHtmlElementAttributes();
		$config = $this->getConfig();
		$classes = [];

		// Theme
		$theme = $config->get( 'Wiki7ThemeDefault' );
		if ( isset( self::CLIENTPREFS_THEME_MAP[$theme] ) ) {
			$classes[] = 'skin-theme-clientpref-' . self::CLIENTPREFS_THEME_MAP[$theme];
		}

		// Default client preferences
		foreach ( self::DEFAULT_CLIENT_PREFS as $feature => $value ) {
			$classes[] = $feature . '-clientpref-' . $value;
		}

		// Header position
		$headerPosition = $config->get( 'Wiki7HeaderPosition' );
		if ( !in_array( $headerPosition, [ 'left', 'right', 'top', 'bottom' ], true ) ) {
			$headerPosition = 'left';
		}
		$classes[] = 'wiki7-header-position-' . $headerPosition;

		$attrs['class'] = trim( $attrs['class'] . ' ' . implode( ' ', $classes ) );
		return $attrs;
	}

	/**
	 * Ensure onSkinTemplateNavigation runs after all SkinTemplateNavigation hooks
	 * @see T287622
	 *
	 * @param SkinTemplate $skin
	 * @param array &$content_navigation
	 */
	protected function runOnSkinTemplateNavigationHooks( SkinTemplate $skin, &$content_navigation ): void {
		parent::runOnSkinTemplateNavigationHooks( $skin, $content_navigation );
		Hooks\SkinHooks::onSkinTemplateNavigation( $skin, $content_navigation );
	}

	/**
	 * Calls getLanguages with caching.
	 * From Vector 2022
	 */
	protected function getLanguagesCached(): array {
		if ( $this->languages === null ) {
			$this->languages = $this->getLanguages();
		}
		return $this->languages;
	}

	/**
	 * @inheritDoc
	 */
	public function getTemplateData(): array {
		$parentData = parent::getTemplateData();
		self::polyfillFooterPortlets( $parentData );

		$config = $this->getConfig();
		$localizer = $this->getContext();
		$lang = $this->getLanguage();
		$out = $this->getOutput();
		$title = $this->getTitle();
		$user = $this->getUser();

		[ $sidebar, $pageToolsMenu ] = $this->extractPageToolsFromSidebar(
			$parentData['data-portlets-sidebar']
		);

		$components = [
			'data-footer' => new Wiki7ComponentFooter(
				$localizer,
				[
					'data-footer-places' => $parentData['data-portlets']['data-footer-places'] ?? [],
					'data-footer-icons' => $parentData['data-portlets']['data-footer-icons'] ?? [],
				]
			),
			'data-main-menu' => new Wiki7ComponentMainMenu( $sidebar ),
			'data-page-footer' => new Wiki7ComponentPageFooter(
				$localizer,
				$parentData['data-portlets']['data-footer-info'] ?? []
			),
			'data-page-heading' => new Wiki7ComponentPageHeading(
				$this->userFactory,
				$this->genderCache,
				$this->userIdentityLookup,
				$this->languageConverterFactory,
				$lang,
				$localizer,
				$out,
				$title,
				$parentData['html-title-heading']
			),
			'data-page-sidebar' => new Wiki7ComponentPageSidebar(
				$localizer,
				$title,
				$parentData['data-last-modified']
			),
			'data-page-tools' => new Wiki7ComponentPageTools(
				$config,
				$localizer,
				$title,
				$user,
				$this->permissionManager,
				count( $this->getLanguagesCached() ),
				$pageToolsMenu,
				// These portlets can be unindexed
				$parentData['data-portlets']['data-languages'] ?? [],
				$parentData['data-portlets']['data-variants'] ?? []
			),
			'data-site-stats' => new Wiki7ComponentSiteStats(
				$config,
				$localizer,
				$lang,
				$this->languageNameUtils
			),
			'data-user-info' => new Wiki7ComponentUserInfo(
				$this->userGroupManager,
				$lang,
				$localizer,
				$title,
				$user,
				$this->tempUserConfig,
				$parentData['data-portlets']['data-user-page']
			),
			'data-sticky-header' => new Wiki7ComponentStickyHeader(
				visualEditorTabPositionFirst: $this->isVisualEditorTabPositionFirst( $parentData['data-portlets']['data-views'] ),
				enableShare: $config->get( 'Wiki7EnableShare' ) && $title->exists() && $title->isContentPage()
			),
			'data-body-content' => new Wiki7ComponentBodyContent(
				$parentData['html-body-content'],
				$this->shouldMakeSections( $config, $title )
			),
			'data-toc' => new Wiki7ComponentTableOfContents(
				$parentData['data-toc'] ?? [],
				$localizer,
				$config
			),
		];

		foreach ( $components as $key => $component ) {
			$parentData[$key] = $component->getTemplateData();
		}

		// TODO: Pass tagline through the component instead of reaching across template data
		$parentData['data-sticky-header']['html-sticky-header-tagline'] =
			$this->prepareStickyHeaderTagline( $parentData['data-page-heading']['html-tagline'] );

		// TODO: Pass the home icon through the component instead of injecting into logos data
		$parentData['data-logos']['icon-home'] = 'home';

		// Drawer footer links (config-driven, optional)
		$footerLinks = $this->getConfig()->get( 'Wiki7DrawerFooterLinks' );
		if ( is_array( $footerLinks ) && count( $footerLinks ) > 0 ) {
			$parentData['array-drawer-footer-items'] = $footerLinks;
		}

		// Captured by SkinHooks::updateNotificationsMenu during the
		// SkinTemplateNavigation hook (which ran inside parent::getTemplateData
		// above). Drives the notifications dropdown in Header.mustache.
		$parentData['data-notifications'] = $this->notificationData;

		$parentData['toc-enabled'] = !empty( $parentData['data-toc'][ 'array-sections' ] );
		if ( $parentData['toc-enabled'] ) {
			// This body class depends on template data so it can't move to
			// getHtmlElementAttributes(). Safe here because getTemplateData()
			// only runs for the active rendering skin.
			$out->addBodyClasses( 'wiki7-toc-enabled' );
		}

		return $parentData;
	}

	/**
	 * Extracts the page tools menu from the sidebar and returns both.
	 * From Vector 2022
	 *
	 * @return array [ $sidebar, $pageToolsMenu ]
	 */
	private function extractPageToolsFromSidebar( array $sidebar ): array {
		$restPortlets = $sidebar[ 'array-portlets-rest' ] ?? [];
		$pageToolsMenu = [];
		$toolboxMenuIndex = array_search(
			Wiki7ComponentPageTools::TOOLBOX_ID,
			array_column(
				$restPortlets,
				'id'
			)
		);

		if ( $toolboxMenuIndex !== false ) {
			$pageToolsMenu = array_splice( $restPortlets, $toolboxMenuIndex, 1 );
			$sidebar['array-portlets-rest'] = $restPortlets;
		}

		return [ $sidebar, $pageToolsMenu ];
	}

	/**
	 * Check whether Visual Editor Tab Position is first
	 * From Vector 2022
	 */
	private function isVisualEditorTabPositionFirst( array $dataViews ): bool {
		foreach ( $dataViews[ 'array-items' ] as $item ) {
			if ( $item[ 'name' ] === 've-edit' ) {
				return true;
			}
			if ( $item[ 'name' ] === 'edit' ) {
				return false;
			}
		}
		return false;
	}

	/**
	 * Check if collapsible sections should be made
	 */
	private function shouldMakeSections( Config $config, Title $title ): bool {
		if (
			$config->get( 'Wiki7EnableCollapsibleSections' ) === false ||
			!$title->canExist() ||
			$title->isMainPage() ||
			!$title->isContentPage() ||
			$title->getContentModel() !== CONTENT_MODEL_WIKITEXT
		) {
			return false;
		}

		// If MF is installed, check if page is in mobile view and let MF do the formatting
		// @phan-suppress-next-line PhanUndeclaredClassMethod MobileFrontend is an optional dependency
		return $this->mfContext === null || !$this->mfContext->shouldDisplayMobileView();
	}

	/**
	 * Prepare the tagline for the sticky header
	 * Replace <a> elements with <span> elements because
	 * you can't nest <a> elements in <a> elements
	 */
	private static function prepareStickyHeaderTagline( string $tagline ): string {
		return preg_replace( '/<a\s+href="([^"]+)"[^>]*>(.*?)<\/a>/', '<span>$2</span>', $tagline ) ?? $tagline;
	}

	/**
	 * Set up skin features that modify the constructor $options array.
	 * Only bodyClasses and styles belong here — OutputPage modifications
	 * are handled by initPage() and getHtmlElementAttributes().
	 */
	private function buildSkinFeatures( array &$options ): void {
		$config = $this->getConfig();
		$title = $this->getOutput()->getTitle();

		if ( $title !== null ) {
			// Collapsible sections
			if (
				$config->get( 'Wiki7EnableCollapsibleSections' ) === true &&
				$title->isContentPage()
			) {
				$options['bodyClasses'][] = 'wiki7-sections-enabled';
			}
		}

		foreach ( self::OPTIONAL_FONT_MODULES as $configKey => $module ) {
			if ( $config->get( $configKey ) === true ) {
				$options['styles'][] = $module;
			}
		}
	}

	/**
	 * Adds metadata to the output page (theme-color and manifest)
	 */
	private function addMetadata( OutputPage $out, Config $config ): void {
		$out->addMeta( 'theme-color', $config->get( 'Wiki7ThemeColor' ) );

		if (
			$config->get( 'Wiki7EnableManifest' ) !== true ||
			$config->get( MainConfigNames::GroupPermissions )['*']['read'] !== true
		) {
			return;
		}

		try {
			$href = $this->urlUtils->expand( wfAppendQuery( wfScript( 'api' ),
					[ 'action' => 'appmanifest' ] ), PROTO_RELATIVE );
		} catch ( BadMethodCallException ) {
			$href = '';
		}

		$out->addLink( [
			'rel' => 'manifest',
			'href' => $href,
		] );
	}

	/**
	 * Normalize a footer menu data array to the canonical Wiki7 shape that
	 * Footer.mustache consumes.
	 *
	 * Different sources of footer menu data carry slightly different outer
	 * fields — SkinComponentFooter::formatFooterDataForCurrentSpec strips
	 * 'class' and injects 'className', whereas raw portlet output (from
	 * SkinComponentMenu via getPortletData) keeps 'class' and never sets
	 * 'className'. This helper standardises on Wiki7's expected shape so
	 * the template can render either source identically.
	 *
	 * @param array $data Source menu data (must include 'array-items' to be usable)
	 * @param bool $isIcons True for footer-icons (toggles 'noprint' className)
	 * @param string $id Canonical DOM id (e.g. 'footer-places')
	 * @return array Canonical Wiki7 footer menu shape
	 */
	private static function normalizeFooterMenu( array $data, bool $isIcons, string $id ): array {
		return [
			'id' => $id,
			'className' => $isIcons ? 'noprint' : null,
			'array-items' => $data['array-items'] ?? [],
		];
	}

	/**
	 * Populate $parentData['data-portlets']['data-footer-{places,info,icons}']
	 * so Wiki7's templates can consume the modern portlet location on every
	 * supported MW version.
	 *
	 * Either-or strategy: if the portlet bucket already carries items
	 * (canonical source on MW 1.47+), keep it but normalize its shape.
	 * Otherwise, build it from the legacy $parentData['data-footer'].*
	 * slice (canonical source on MW 1.43–1.46). When both are empty, the
	 * key is set to [] so Mustache skips the empty <nav> render.
	 *
	 * Trade-off: on MW 1.43–1.46, an extension that uses
	 * SkinTemplateNavigation::Universal directly with a footer-* key would
	 * populate only the portlet bucket, and this strategy would then NOT
	 * fall back to the legacy bucket — so the built-in privacy/about/
	 * disclaimer links would be dropped. This is accepted as a rare edge
	 * case (the MW manual documents that the new hook "only applies to
	 * modern skins using that menu"). T426358 forward-compat for MW 1.47
	 * is the primary scope.
	 *
	 * @param array &$parentData The full parent template data array
	 */
	private static function polyfillFooterPortlets( array &$parentData ): void {
		if ( !isset( $parentData['data-portlets'] ) || !is_array( $parentData['data-portlets'] ) ) {
			$parentData['data-portlets'] = [];
		}

		$pairs = [
			[ 'data-footer-places', 'data-places', false, 'footer-places' ],
			[ 'data-footer-info', 'data-info', false, 'footer-info' ],
			[ 'data-footer-icons', 'data-icons', true, 'footer-icons' ],
		];

		foreach ( $pairs as [ $portletKey, $legacyKey, $isIcons, $id ] ) {
			$portlet = $parentData['data-portlets'][ $portletKey ] ?? null;
			$legacy = $parentData['data-footer'][ $legacyKey ] ?? null;

			if ( !empty( $portlet['array-items'] ) ) {
				$source = $portlet;
			} elseif ( !empty( $legacy['array-items'] ) ) {
				$source = $legacy;
			} else {
				$parentData['data-portlets'][ $portletKey ] = [];
				continue;
			}

			$parentData['data-portlets'][ $portletKey ] = self::normalizeFooterMenu( $source, $isIcons, $id );
		}
	}

}
