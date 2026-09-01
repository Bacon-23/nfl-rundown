/*
 * Trinity Rundown — progressive enhancement.
 *
 * The markup ships as a list of <details> panels that already work with no
 * JavaScript. On wide screens this swaps them for a tab strip. If this file
 * fails to load, or the screen is narrow, the accordion is the experience.
 *
 * No dependencies, no build step.
 */
( function () {
	'use strict';

	var TAB_MIN_WIDTH = 720;

	function init( week ) {
		var games = Array.prototype.slice.call( week.querySelectorAll( '.trun-game' ) );
		if ( games.length < 2 ) {
			return;
		}

		var media = window.matchMedia( '(min-width: ' + TAB_MIN_WIDTH + 'px)' );
		var tablist = buildTablist( week, games );

		function apply() {
			if ( media.matches ) {
				enableTabs( week, games, tablist );
			} else {
				disableTabs( week, games, tablist );
			}
		}

		apply();
		media.addEventListener( 'change', apply );
	}

	function buildTablist( week, games ) {
		var tablist = document.createElement( 'div' );
		tablist.className = 'trun-tablist';
		tablist.setAttribute( 'role', 'tablist' );
		tablist.hidden = true;

		games.forEach( function ( game, index ) {
			var teams = game.querySelector( '.trun-game__teams' );
			var tab = document.createElement( 'button' );

			tab.type = 'button';
			tab.className = 'trun-tab';
			tab.textContent = teams ? teams.textContent : 'Game ' + ( index + 1 );
			tab.setAttribute( 'role', 'tab' );
			tab.setAttribute( 'aria-controls', game.id );
			tab.setAttribute( 'aria-selected', index === 0 ? 'true' : 'false' );
			tab.tabIndex = index === 0 ? 0 : -1;

			tab.addEventListener( 'click', function () {
				select( games, tablist, index );
			} );

			tab.addEventListener( 'keydown', function ( event ) {
				var delta = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity }[ event.key ];
				if ( delta === undefined ) {
					return;
				}
				event.preventDefault();

				var next = delta === -Infinity ? 0
					: delta === Infinity ? games.length - 1
					: ( index + delta + games.length ) % games.length;

				select( games, tablist, next );
				tablist.children[ next ].focus();
			} );

			tablist.appendChild( tab );
		} );

		var container = week.querySelector( '.trun-games' );
		week.insertBefore( tablist, container );

		return tablist;
	}

	function select( games, tablist, index ) {
		games.forEach( function ( game, i ) {
			game.hidden = i !== index;
			game.open = true;
		} );

		Array.prototype.forEach.call( tablist.children, function ( tab, i ) {
			tab.setAttribute( 'aria-selected', i === index ? 'true' : 'false' );
			tab.tabIndex = i === index ? 0 : -1;
		} );
	}

	function enableTabs( week, games, tablist ) {
		week.setAttribute( 'data-tabs', 'on' );
		tablist.hidden = false;

		games.forEach( function ( game ) {
			game.setAttribute( 'role', 'tabpanel' );
		} );

		// Keep whichever panel the reader already had open.
		var openIndex = games.findIndex( function ( game ) {
			return game.open && ! game.hidden;
		} );
		select( games, tablist, openIndex === -1 ? 0 : openIndex );
	}

	function disableTabs( week, games, tablist ) {
		week.removeAttribute( 'data-tabs' );
		tablist.hidden = true;

		games.forEach( function ( game, index ) {
			game.hidden = false;
			game.removeAttribute( 'role' );
			game.open = index === 0;
		} );
	}

	function boot() {
		document.querySelectorAll( '.trun-week' ).forEach( init );
	}

	if ( document.readyState === 'loading' ) {
		document.addEventListener( 'DOMContentLoaded', boot );
	} else {
		boot();
	}
}() );
