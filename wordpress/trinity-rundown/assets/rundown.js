/*
 * Trinity Rundown -- the expand-all control.
 *
 * The panels themselves are <details>: the browser opens and closes them and
 * this file has nothing to do with that. All it adds is the one thing markup
 * cannot do, which is act on every panel at once.
 *
 * It is written as an enhancement, not a dependency. The button is rendered
 * `hidden` by PHP and unhidden here, so a reader whose script never runs gets
 * the page as it was rather than a dead control. Nothing else on the page is
 * touched, and no string lives in here -- the labels come off the button's own
 * data attributes so they stay translatable in PHP.
 */
( function () {
	'use strict';

	/**
	 * Point the button at whichever action is left.
	 *
	 * Called after our own clicks *and* after the reader opens a panel on
	 * their own, so opening the last closed game by hand flips the button to
	 * "Collapse all" rather than leaving it offering something already done.
	 */
	function sync( button, games ) {
		var allOpen = games.every( function ( game ) {
			return game.open;
		} );

		button.textContent = allOpen
			? button.dataset.labelCollapse
			: button.dataset.labelExpand;
	}

	function wire( week ) {
		var button = week.querySelector( '[data-trun-toggle-all]' );
		if ( ! button ) {
			return;
		}

		// Direct children only: a <details> inside a game body is not a game.
		var games = Array.prototype.slice.call(
			week.querySelectorAll( '.trun-games > .trun-game' )
		);
		if ( games.length < 2 ) {
			return;
		}

		button.addEventListener( 'click', function () {
			/*
			 * Anything still closed means the reader wants everything open --
			 * so the button only collapses once there is nothing left to open.
			 * Reading the panels each time rather than tracking a flag keeps
			 * this right even when a panel was toggled by hand, or by the
			 * browser following a link to a game's anchor.
			 */
			var open = games.some( function ( game ) {
				return ! game.open;
			} );

			games.forEach( function ( game ) {
				game.open = open;
			} );

			sync( button, games );
		} );

		/*
		 * `toggle` does not bubble, so it is listened for on each panel rather
		 * than once on the week.
		 */
		games.forEach( function ( game ) {
			game.addEventListener( 'toggle', function () {
				sync( button, games );
			} );
		} );

		sync( button, games );

		// Last: the control is only shown once it is known to work.
		button.closest( '.trun-toolbar' ).hidden = false;
	}

	Array.prototype.forEach.call(
		document.querySelectorAll( '.trun-week' ),
		wire
	);
} )();
