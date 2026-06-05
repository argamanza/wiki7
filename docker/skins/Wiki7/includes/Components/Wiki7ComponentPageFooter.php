<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Components;

use MessageLocalizer;

/**
 * Wiki7ComponentPageFooter component
 *
 * Consumes $parentData['data-portlets']['data-footer-info'] (polyfilled
 * by SkinWiki7::polyfillFooterPortlets() for MW 1.43–1.46 compatibility).
 */
class Wiki7ComponentPageFooter implements Wiki7Component {

	public function __construct(
		private readonly MessageLocalizer $localizer,
		private readonly array $footerData
	) {
	}

	public function getTemplateData(): array {
		$footerData = $this->footerData;

		if ( !isset( $footerData['array-items'] ) ) {
			return $footerData;
		}

		foreach ( $footerData['array-items'] as &$item ) {
			$name = $item['name'] ?? '';
			$msg = $this->localizer->msg( 'wiki7-page-info-' . $name );
			$item['label'] = $msg->exists() ? $msg->text() : ( $item['label'] ?? '' );
		}

		return $footerData;
	}
}
