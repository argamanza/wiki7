<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Components;

use MediaWiki\Title\Title;
use MessageLocalizer;

/**
 * Wiki7ComponentPageSidebar component
 */
class Wiki7ComponentPageSidebar implements Wiki7Component {

	public function __construct(
		private readonly MessageLocalizer $localizer,
		private readonly Title $title,
		private readonly array $lastModifiedData
	) {
	}

	private function getLastModData(): array {
		$lastModifiedData = $this->lastModifiedData;
		$timestamp = $this->lastModifiedData['timestamp'];

		if ( $timestamp === null ) {
			return [];
		}

		return [
			'id' => 'wiki7-sidebar-lastmod',
			'label' => $this->localizer->msg( 'wiki7-page-info-lastmod' ),
			'array-list-items' => [
				'item-id' => 'lm-time',
				'item-class' => 'mw-list-item',
				'array-links' => [
					'array-attributes' => [
						[
							'key' => 'id',
							'value' => 'wiki7-lastmod-relative'
						],
						[
							'key' => 'href',
							'value' => $this->title->getLocalURL( [ 'diff' => '' ] )
						],
						[
							'key' => 'title',
							'value' => trim( $lastModifiedData['text'] )
						],
						[
							'key' => 'data-timestamp',
							'value' => wfTimestamp( TS_UNIX, $lastModifiedData['timestamp'] )
						]
					],
					'icon' => 'history',
					'text' => $lastModifiedData['date']
				]
			]
		];
	}

	public function getTemplateData(): array {
		return [
			'data-page-sidebar-lastmod' => $this->getLastModData()
		];
	}
}
