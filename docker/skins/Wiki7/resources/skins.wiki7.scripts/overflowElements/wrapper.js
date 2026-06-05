/**
 * Creates the DOM wrapper structure for an overflow element.
 * Handles inherited class migration (floatleft, floatright, etc.)
 * and optional navigation buttons for pointer devices.
 *
 * @param {Object} params
 * @param {Document} params.document
 * @param {Object} params.mw
 * @param {HTMLElement} params.element
 * @param {boolean} params.isPointerDevice
 * @param {string[]} params.inheritedClasses
 * @return {{wrapper: HTMLElement, content: HTMLElement, nav: HTMLElement|null}|null}
 */
function createOverflowWrapper( { document, mw, element, isPointerDevice, inheritedClasses } ) {
	if ( !element || !element.parentNode ) {
		mw.log.error(
			'[Wiki7] Element or element.parentNode is null or undefined.'
		);
		return null;
	}

	try {
		const fragment = document.createDocumentFragment();

		const wrapper = document.createElement( 'div' );
		wrapper.className = 'wiki7-overflow-wrapper';
		fragment.appendChild( wrapper );

		// Migrate inherited classes from element to wrapper
		if ( inheritedClasses ) {
			inheritedClasses.forEach( ( cls ) => {
				if ( element.classList.contains( cls ) ) {
					wrapper.classList.add( cls );
					element.classList.remove( cls );
				}
			} );
		}

		let nav = null;
		if ( isPointerDevice ) {
			const createButton = ( type ) => {
				const button = document.createElement( 'button' );
				button.className = `wiki7-overflow-navButton wiki7-overflow-navButton-${ type } wiki7-ui-icon mw-ui-icon-wikimedia-collapse`;
				button.setAttribute( 'aria-hidden', 'true' );
				button.setAttribute( 'tabindex', '-1' );
				return button;
			};

			nav = document.createElement( 'div' );
			nav.className = 'wiki7-overflow-nav';
			nav.appendChild( createButton( 'left' ) );
			nav.appendChild( createButton( 'right' ) );
			wrapper.appendChild( nav );
		}

		const content = document.createElement( 'div' );
		content.className = 'wiki7-overflow-content';
		wrapper.appendChild( content );

		const parentNode = element.parentNode;
		parentNode.insertBefore( fragment, element );
		content.appendChild( element );

		return { wrapper, content, nav };
	} catch ( error ) {
		mw.log.error(
			`[Wiki7] Error occurred while wrapping element: ${ error.message }`
		);
		return null;
	}
}

module.exports = { createOverflowWrapper };
