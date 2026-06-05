<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Components;

use MessageLocalizer;

/**
 * Wiki7ComponentFooter component
 *
 * Consumes the polyfilled $parentData['data-portlets'] footer slice
 * (data-footer-places, data-footer-icons) produced by
 * SkinWiki7::polyfillFooterPortlets().
 */
class Wiki7ComponentFooter implements Wiki7Component {

	public function __construct(
		private readonly MessageLocalizer $localizer,
		private readonly array $footerPortlets
	) {
	}

	public function getTemplateData(): array {
		return [
			'data-footer-places' => $this->footerPortlets['data-footer-places'] ?? [],
			'data-footer-icons' => $this->footerPortlets['data-footer-icons'] ?? [],
			'msg-wiki7-footer-desc' => $this->localizer
				->msg( 'wiki7-footer-desc' )->inContentLanguage()->parse(),
			'msg-wiki7-footer-tagline' => $this->localizer
				->msg( 'wiki7-footer-tagline' )->inContentLanguage()->parse(),
		];
	}
}
