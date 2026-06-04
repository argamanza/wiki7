/**
 * @param {Object} deps
 * @param {Document} deps.document
 * @param {HTMLElement} deps.bodyContent
 * @return {Object}
 */
function createSections( { document, bodyContent } ) {
	/**
	 * Set up functionality of collapsable sections
	 *
	 * @return {void}
	 */
	function init() {
		if ( !document.body.classList.contains( 'wiki7-sections-enabled' ) ) {
			return;
		}

		const onEditSectionClick = ( e ) => {
			e.stopPropagation();
		};

		const handleClick = ( e ) => {
			const target = e.target;
			const isEditSection = target.closest( '.mw-editsection, .mw-editsection-like' );

			if ( isEditSection ) {
				onEditSectionClick( e );
				return;
			}

			const heading = target.closest( '.wiki7-section-heading' );

			if ( heading && heading.nextElementSibling && heading.nextElementSibling.classList.contains( 'wiki7-section' ) ) {
				const section = heading.nextElementSibling;

				if ( section ) {
					section.hidden = section.hidden ? false : 'until-found';
				}
			}
		};

		bodyContent.addEventListener( 'click', handleClick, false );
	}

	return { init };
}

module.exports = { createSections };
