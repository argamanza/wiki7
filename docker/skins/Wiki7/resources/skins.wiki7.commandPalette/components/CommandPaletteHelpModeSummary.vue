<template>
	<div class="wiki7-command-palette-help__mode-summary">
		<cdx-thumbnail
			v-if="mode.icon"
			:placeholder-icon="mode.icon"
			class="wiki7-command-palette-help__mode-thumbnail"
		></cdx-thumbnail>
		<div class="wiki7-command-palette-help__mode-text">
			<div class="wiki7-command-palette-help__mode-label">
				{{ mode.label || mode.id }}
			</div>
			<div
				v-if="mode.description"
				class="wiki7-command-palette-help__mode-description"
			>
				{{ mode.description }}
			</div>
		</div>
	</div>
</template>

<script>
const { defineComponent } = require( 'vue' );
const { CdxThumbnail } = mw.loader.require( 'skins.wiki7.commandPalette.codex' );

// @vue/component
module.exports = exports = defineComponent( {
	name: 'CommandPaletteHelpModeSummary',
	compilerOptions: {
		whitespace: 'condense'
	},
	components: {
		CdxThumbnail
	},
	props: {
		mode: {
			type: Object,
			required: true
		}
	}
} );
</script>
