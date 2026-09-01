<?php
/**
 * The single shortcode the weekly post contains: [rundown_week season="2026" week="1"]
 */

defined( 'ABSPATH' ) || exit;

add_shortcode( 'rundown_week', 'trun_shortcode_week' );

function trun_shortcode_week( $atts ): string {
	$atts = shortcode_atts(
		[
			'season' => '',
			'week'   => '',
		],
		$atts,
		'rundown_week'
	);

	$season = (int) $atts['season'];
	$week   = (int) $atts['week'];

	if ( $season < 1999 || $week < 1 || $week > 22 ) {
		return current_user_can( 'edit_posts' )
			? '<p class="trun-empty">[rundown_week] needs a valid season and week.</p>'
			: '';
	}

	// Only enqueue on pages that actually contain a rundown.
	wp_enqueue_style( 'trinity-rundown' );
	wp_enqueue_script( 'trinity-rundown' );

	return trun_render_week( $season, $week );
}

add_action( 'wp_enqueue_scripts', 'trun_register_assets' );

function trun_register_assets(): void {
	wp_register_style(
		'trinity-rundown',
		TRUN_URL . 'assets/rundown.css',
		[],
		TRUN_VERSION
	);

	wp_register_script(
		'trinity-rundown',
		TRUN_URL . 'assets/rundown.js',
		[],
		TRUN_VERSION,
		[
			'strategy'  => 'defer',
			'in_footer' => true,
		]
	);
}
