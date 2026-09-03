/*
 * Trinity Rundown — admin screen.
 *
 * One job: make Publish and Unlock ask first. Both are recoverable (Unlock
 * undoes a Publish, and a Publish can be repeated), but neither is something
 * to do by reflex on the way past.
 *
 * Progressive, like the front end: with this file blocked the buttons still
 * work, they just do not ask.
 */
( function () {
	'use strict';

	document.addEventListener( 'click', function ( event ) {
		var button = event.target.closest( '[data-trun-confirm]' );

		if ( ! button ) {
			return;
		}

		if ( ! window.confirm( button.getAttribute( 'data-trun-confirm' ) ) ) {
			event.preventDefault();
		}
	} );
}() );
