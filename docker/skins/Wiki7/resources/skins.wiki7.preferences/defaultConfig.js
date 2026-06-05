const { PreferencesConfig } = require( './types.js' );

/**
 * Built-in default preferences configuration.
 * Uses the same schema as admin overrides from MediaWiki:Wiki7-preferences.json.
 *
 * @return {PreferencesConfig} Default config with sections and preferences
 */
function getDefaultConfig() {
	return {
		sections: {
			appearance: { labelMsg: 'wiki7-preferences-section-appearance' },
			behavior: { labelMsg: 'wiki7-preferences-section-behavior' }
		},
		preferences: {
			'skin-theme': {
				section: 'appearance',
				options: [
					{ value: 'os', labelMsg: 'wiki7-theme-os-label' },
					{ value: 'day', labelMsg: 'wiki7-theme-day-label' },
					{ value: 'night', labelMsg: 'wiki7-theme-night-label' }
				],
				type: 'radio',
				columns: 3,
				labelMsg: 'wiki7-theme-name',
				descriptionMsg: 'wiki7-theme-description',
				visibilityCondition: 'always'
			},
			'wiki7-feature-custom-font-size': {
				section: 'appearance',
				options: [
					{ value: 'small', labelMsg: 'wiki7-feature-custom-font-size-small-label' },
					{ value: 'standard', labelMsg: 'wiki7-feature-custom-font-size-standard-label' },
					{ value: 'large', labelMsg: 'wiki7-feature-custom-font-size-large-label' },
					{ value: 'xlarge', labelMsg: 'wiki7-feature-custom-font-size-xlarge-label' }
				],
				type: 'select',
				labelMsg: 'wiki7-feature-custom-font-size-name',
				descriptionMsg: 'wiki7-feature-custom-font-size-description',
				visibilityCondition: 'always'
			},
			'wiki7-feature-custom-width': {
				section: 'appearance',
				options: [
					{ value: 'standard', labelMsg: 'wiki7-feature-custom-width-standard-label' },
					{ value: 'wide', labelMsg: 'wiki7-feature-custom-width-wide-label' },
					{ value: 'full', labelMsg: 'wiki7-feature-custom-width-full-label' }
				],
				type: 'select',
				labelMsg: 'wiki7-feature-custom-width-name',
				descriptionMsg: 'wiki7-feature-custom-width-description',
				visibilityCondition: 'always'
			},
			// Switch preferences use short-form options (strings).
			// normalizeConfig() converts these to { value: '0' } / { value: '1' }.
			'wiki7-feature-pure-black': {
				section: 'appearance',
				options: [ '0', '1' ],
				type: 'switch',
				labelMsg: 'wiki7-feature-pure-black-name',
				descriptionMsg: 'wiki7-feature-pure-black-description',
				visibilityCondition: 'dark-theme'
			},
			'wiki7-feature-image-dimming': {
				section: 'appearance',
				options: [ '0', '1' ],
				type: 'switch',
				labelMsg: 'wiki7-feature-image-dimming-name',
				descriptionMsg: 'wiki7-feature-image-dimming-description',
				visibilityCondition: 'dark-theme'
			},
			'wiki7-feature-autohide-navigation': {
				section: 'behavior',
				options: [ '0', '1' ],
				type: 'switch',
				labelMsg: 'wiki7-feature-autohide-navigation-name',
				descriptionMsg: 'wiki7-feature-autohide-navigation-description',
				visibilityCondition: 'tablet-viewport'
			},
			'wiki7-feature-performance-mode': {
				section: 'behavior',
				options: [ '0', '1' ],
				type: 'switch',
				labelMsg: 'wiki7-feature-performance-mode-name',
				descriptionMsg: 'wiki7-feature-performance-mode-description',
				visibilityCondition: 'always'
			}
		}
	};
}

module.exports = getDefaultConfig;
