<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Hooks;

use MediaWiki\Config\Config;
use MediaWiki\MainConfigNames;
use MediaWiki\MediaWikiServices;
use MediaWiki\Registration\ExtensionRegistry;
use MediaWiki\ResourceLoader as RL;
use MediaWiki\Skins\Wiki7\OnWikiJsonReader;
use MediaWiki\Skins\Wiki7\PreferencesConfigProvider;
use MediaWiki\Skins\Wiki7\ShareConfigProvider;

/**
 * Hooks to run relating to the resource loader
 */
class ResourceLoaderHooks {

	/**
	 * Passes config variables to skins.wiki7.scripts ResourceLoader module.
	 * @param RL\Context $context
	 * @param Config $config
	 * @return array
	 */
	public static function getWiki7ResourceLoaderConfig(
		RL\Context $context,
		Config $config
	) {
		return [
			'wgWiki7EnablePreferences' => $config->get( 'Wiki7EnablePreferences' ),
			'wgWiki7OverflowInheritedClasses' => $config->get( 'Wiki7OverflowInheritedClasses' ),
			'wgWiki7OverflowNowrapClasses' => $config->get( 'Wiki7OverflowNowrapClasses' ),
			'wgWiki7ShareMode' => $config->get( 'Wiki7ShareMode' ),
		];
	}

	/**
	 * Passes config variables to skins.wiki7.preferences ResourceLoader module.
	 * @param RL\Context $context
	 * @param Config $config
	 * @return array
	 */
	public static function getWiki7PreferencesResourceLoaderConfig(
		RL\Context $context,
		Config $config
	) {
		return [
			'wgWiki7ThemeDefault' => $config->get( 'Wiki7ThemeDefault' ),
		];
	}

	/**
	 * Passes config variables to skins.wiki7.commandPalette ResourceLoader module.
	 * @param RL\Context $context
	 * @param Config $config
	 * @return array
	 */
	public static function getWiki7CommandPaletteResourceLoaderConfig(
		RL\Context $context,
		Config $config
	) {
		$extensionRegistry = ExtensionRegistry::getInstance();

		return [
			'isSemanticMediaWikiEnabled' => $extensionRegistry->isLoaded( 'SemanticMediaWiki' ),
			'wgSearchSuggestCacheExpiry' => $config->get( MainConfigNames::SearchSuggestCacheExpiry )
		];
	}

	/**
	 * Passes config variables to skins.wiki7.share ResourceLoader module.
	 * @param RL\Context $context
	 * @param Config $config
	 * @return array{services: array, urlShortener: array{available: bool, qrAvailable: bool}}
	 */
	public static function getWiki7ShareResourceLoaderConfig(
		RL\Context $context,
		Config $config
	): array {
		$mwServices = MediaWikiServices::getInstance();
		$provider = new ShareConfigProvider(
			new OnWikiJsonReader(
				$mwServices->getRevisionLookup(),
				$mwServices->getTitleFactory()
			),
			$mwServices->getUrlUtils()
		);

		$extensionRegistry = ExtensionRegistry::getInstance();
		$urlShortenerLoaded = $extensionRegistry->isLoaded( 'UrlShortener' );
		$qrAvailable = $urlShortenerLoaded
			&& $config->has( 'UrlShortenerEnableQrCode' )
			&& (bool)$config->get( 'UrlShortenerEnableQrCode' );

		return [
			'services' => $provider->getServiceOptions() ?? [],
			'urlShortener' => [
				'available' => $urlShortenerLoaded,
				'qrAvailable' => $qrAvailable,
			],
		];
	}

	/**
	 * Return on-wiki preferences overrides with pre-resolved message texts.
	 *
	 * @param RL\Context $context
	 * @param Config $config
	 * @return array{overrides: ?array, messages: \stdClass|array<string, string>}
	 */
	public static function getWiki7PreferencesOverrides(
		RL\Context $context,
		Config $config
	): array {
		$services = MediaWikiServices::getInstance();
		$provider = new PreferencesConfigProvider(
			new OnWikiJsonReader(
				$services->getRevisionLookup(),
				$services->getTitleFactory()
			),
			$context
		);
		return $provider->getOverrides( $context->getLanguage() );
	}
}
