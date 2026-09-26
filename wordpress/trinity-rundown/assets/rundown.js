/*
 * Trinity Rundown -- the expand-all control, and the tabs inside each game.
 *
 * The panels themselves are <details>: the browser opens and closes them and
 * this file has nothing to do with that. It adds the two things markup cannot
 * do: act on every panel at once, and show one part of a game at a time.
 *
 * Both are enhancements, not dependencies. The button and every tab strip are
 * rendered `hidden` by PHP and unhidden here, so a reader whose script never
 * runs gets every game's content stacked rather than a dead control. No string
 * lives in here -- labels come off the markup so they stay translatable in PHP.
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

	/**
	 * Show one tab's panel and hide the rest, keeping the ARIA state and the
	 * roving tabindex in step: only the selected tab is in the Tab order, and
	 * the arrow keys move between the others.
	 */
	function select( tabs, panels, chosen, focus ) {
		tabs.forEach( function ( tab, index ) {
			var on = tab === chosen;
			tab.setAttribute( 'aria-selected', on ? 'true' : 'false' );
			tab.tabIndex = on ? 0 : -1;
			panels[ index ].hidden = ! on;
		} );

		if ( focus ) {
			chosen.focus();
		}
	}

	function wireTabs( group ) {
		// Its own strip only. A group with a single tab renders no strip, so a
		// plain descendant query could reach into a nested group and wire it
		// twice.
		var list = group.querySelector( ':scope > .trun-tablist' );
		if ( ! list ) {
			return;
		}

		var tabs = Array.prototype.slice.call(
			list.querySelectorAll( '[data-trun-tab]' )
		);

		// Tabs and panels are rendered in the same order, one each.
		var panels = tabs.map( function ( tab ) {
			return document.getElementById( tab.getAttribute( 'aria-controls' ) );
		} );
		if ( panels.indexOf( null ) !== -1 ) {
			return;
		}

		list.setAttribute( 'role', 'tablist' );
		tabs.forEach( function ( tab, index ) {
			tab.setAttribute( 'role', 'tab' );
			panels[ index ].setAttribute( 'role', 'tabpanel' );
			panels[ index ].setAttribute( 'aria-labelledby', tab.id );
			panels[ index ].tabIndex = 0;

			tab.addEventListener( 'click', function () {
				select( tabs, panels, tab, false );
			} );
		} );

		list.addEventListener( 'keydown', function ( event ) {
			var current = tabs.indexOf( document.activeElement );
			var next;

			if ( current === -1 ) {
				return;
			}

			switch ( event.key ) {
				case 'ArrowRight':
					next = ( current + 1 ) % tabs.length;
					break;
				case 'ArrowLeft':
					next = ( current - 1 + tabs.length ) % tabs.length;
					break;
				case 'Home':
					next = 0;
					break;
				case 'End':
					next = tabs.length - 1;
					break;
				default:
					return;
			}

			event.preventDefault();
			select( tabs, panels, tabs[ next ], true );
		} );

		// The first tab PHP left standing is the default -- Rundown, when it has anything.
		select( tabs, panels, tabs[ 0 ], false );

		// Last: the strip is only shown once it is known to work.
		list.hidden = false;
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

	Array.prototype.forEach.call(
		document.querySelectorAll( '[data-trun-tabs]' ),
		wireTabs
	);
} )();
