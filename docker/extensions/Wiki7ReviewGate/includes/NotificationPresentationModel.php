<?php

namespace MediaWiki\Extension\Wiki7ReviewGate;

use MediaWiki\Extension\Notifications\Formatters\EchoEventPresentationModel;

/**
 * Minimal presentation model for the wiki7-bot-review-pending-{draft,update}
 * notification types. Renders the i18n header messages from the extension's
 * notification entries and links the user straight to the page in question.
 */
class NotificationPresentationModel extends EchoEventPresentationModel {

	public function getIconType() {
		return 'edit'; // closest built-in Echo icon; could ship a custom 'review' icon later
	}

	public function getHeaderMessage() {
		// notification-header-wiki7-bot-review-pending-{draft,update}
		// title-params are [agent (user), title (link)] — Echo's default
		// EchoEventPresentationModel populates these from the event's title +
		// agent fields, which we set in EchoEvent::create() at fire time.
		$message = parent::getHeaderMessage();
		$message->params( $this->getAgentForOutput() );
		$message->params( $this->getTruncatedTitleText( $this->event->getTitle(), true ) );
		return $message;
	}

	public function getPrimaryLink() {
		$title = $this->event->getTitle();
		if ( $title === null ) {
			return false;
		}
		return [
			'url'   => $title->getLocalURL(),
			'label' => $this->msg( 'notification-link-text-view-wiki7-bot-review-pending' )->text(),
		];
	}
}
