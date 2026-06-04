<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Components;

/**
 * Wiki7ComponentMainMenu component
 */
class Wiki7ComponentMainMenu implements Wiki7Component {

	public function __construct(
		private readonly array $sidebarData
	) {
	}

	public function getTemplateData(): array {
		return [
			'data-portlets-first' => (
				new Wiki7ComponentMenu( $this->sidebarData['data-portlets-first'] )
			)->getTemplateData(),
			'array-portlets-rest' => array_map(
				static fn ( array $data ): array => ( new Wiki7ComponentMenu( $data ) )->getTemplateData(),
				$this->sidebarData[ 'array-portlets-rest' ]
			)
		];
	}
}
