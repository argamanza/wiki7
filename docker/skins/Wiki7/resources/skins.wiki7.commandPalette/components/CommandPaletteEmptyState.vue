<template>
	<div class="wiki7-command-palette-empty-state">
		<div class="wiki7-command-palette-empty-state__icon">
			<slot name="icon">
				<cdx-icon :icon="icon"></cdx-icon>
			</slot>
		</div>
		<div class="wiki7-command-palette-empty-state__content">
			<div class="wiki7-command-palette-empty-state__title">
				{{ title }}
			</div>
			<div
				v-if="description"
				class="wiki7-command-palette-empty-state__description"
			>
				{{ description }}
			</div>
		</div>
	</div>
</template>

<script>
const { defineComponent } = require( 'vue' );
const { CdxIcon } = mw.loader.require( 'skins.wiki7.commandPalette.codex' );

// @vue/component
module.exports = exports = defineComponent( {
	name: 'CommandPaletteEmptyState',
	components: {
		CdxIcon
	},
	props: {
		title: {
			type: String,
			required: true
		},
		description: {
			type: String,
			default: ''
		},
		icon: {
			type: [ String, Object ],
			default: 'cdxIconInfo'
		}
	}
} );
</script>

<style lang="less">
@import 'mediawiki.skin.variables.less';
@import '../mixins.less';

.wiki7-command-palette-empty-state {
	display: flex;
	flex-direction: column;
	gap: var( --space-sm );
	align-items: center;
	justify-content: center;
	padding: var( --space-xxl ) var( --wiki7-command-palette-side-padding );
	text-align: center;

	&__icon {
		width: 5rem;
		height: 5rem;

		.cdx-icon {
			width: 100%;
			height: 100%;
		}
	}

	&__content {
		display: flex;
		flex-direction: column;
	}

	&__title {
		.mixin-wiki7-font-styles( 'heading-4' );
		color: var( --color-emphasized );
	}

	&__description {
		.mixin-wiki7-font-styles( 'body' );
		color: var( --color-subtle );
	}
}
</style>
