<?php

declare( strict_types=1 );

namespace MediaWiki\Skins\Wiki7\Api;

use MediaWiki\Api\ApiFormatJson;

/**
 * T282500
 * TODO: This should be merged to core
 */
class ApiWebappManifestFormatJson extends ApiFormatJson {

	/**
	 * Return the proper content-type
	 */
	public function getMimeType(): string {
		return 'application/manifest+json';
	}
}
