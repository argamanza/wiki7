<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Components;

use MediaWiki\Config\Config;
use MessageLocalizer;

/**
 * Wiki7ComponentTableOfContents component
 *
 * Enriches MW core's data-toc with Wiki7-specific template data.
 */
class Wiki7ComponentTableOfContents implements Wiki7Component {

	public function __construct(
		private array $tocData,
		private readonly MessageLocalizer $localizer,
		private readonly Config $config
	) {
	}

	public function getTemplateData(): array {
		$sections = $this->tocData['array-sections'] ?? [];
		if ( !$sections ) {
			return [];
		}

		foreach ( $sections as &$section ) {
			if ( $section['is-top-level-section'] && $section['is-parent-section'] ) {
				$section['wiki7-button-label'] =
					// @phan-suppress-next-line SecurityCheck-XSS $section['line'] is pre-escaped HTML from the parser
					$this->localizer->msg( 'wiki7-toc-toggle-button-label' )
						->rawParams( $section['line'] )
						->escaped();
			}
		}

		$this->tocData['array-sections'] = $sections;

		return array_merge( $this->tocData, [
			'wiki7-is-collapse-sections-enabled' =>
				count( $sections ) > 3 &&
				( $this->tocData['number-section-count'] ?? 0 ) >=
					$this->config->get( 'Wiki7TableOfContentsCollapseAtCount' ),
		] );
	}
}
