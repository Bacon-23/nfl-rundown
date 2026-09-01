<?php
/**
 * REST ingest endpoint for the stats pipeline.
 *
 * Authentication is a bearer token compared against a wp-config.php constant
 * rather than an Application Password. WordPress.com accounts authenticate
 * through WP.com SSO, where the Application Passwords UI is not dependable,
 * and this endpoint only ever needs one machine credential.
 *
 * The route can write stats rows and nothing else. It cannot create, edit, or
 * publish posts -- publishing stays a deliberate human action in wp-admin.
 */

defined( 'ABSPATH' ) || exit;

const TRUN_REST_NAMESPACE = 'trinity-rundown/v1';

add_action( 'rest_api_init', 'trun_register_rest_routes' );

function trun_register_rest_routes(): void {
	register_rest_route(
		TRUN_REST_NAMESPACE,
		'/week',
		[
			'methods'             => 'POST',
			'callback'            => 'trun_rest_ingest_week',
			'permission_callback' => 'trun_rest_authorize',
			'args'                => [
				'season' => [
					'required'          => true,
					'type'              => 'integer',
					'validate_callback' => static fn( $v ) => $v >= 1999 && $v <= 2100,
				],
				'week'   => [
					'required'          => true,
					'type'              => 'integer',
					'validate_callback' => static fn( $v ) => $v >= 1 && $v <= 22,
				],
				'games'  => [
					'required' => true,
					'type'     => 'array',
				],
			],
		]
	);

	// Lightweight probe so the pipeline can verify auth and schema without
	// writing anything. Used by the Phase 0 smoke test and by CI.
	register_rest_route(
		TRUN_REST_NAMESPACE,
		'/health',
		[
			'methods'             => 'GET',
			'callback'            => 'trun_rest_health',
			'permission_callback' => 'trun_rest_authorize',
		]
	);
}

/**
 * Bearer-token check against the TRINITY_RUNDOWN_TOKEN constant.
 */
function trun_rest_authorize( WP_REST_Request $request ) {
	if ( ! defined( 'TRINITY_RUNDOWN_TOKEN' ) || '' === (string) TRINITY_RUNDOWN_TOKEN ) {
		return new WP_Error(
			'trun_not_configured',
			'TRINITY_RUNDOWN_TOKEN is not defined in wp-config.php.',
			[ 'status' => 503 ]
		);
	}

	if ( ! is_ssl() && 'local' !== wp_get_environment_type() ) {
		return new WP_Error( 'trun_insecure', 'This endpoint requires HTTPS.', [ 'status' => 400 ] );
	}

	$header = (string) $request->get_header( 'authorization' );
	if ( ! preg_match( '/^Bearer\s+(.+)$/i', trim( $header ), $m ) ) {
		return new WP_Error( 'trun_no_token', 'Missing bearer token.', [ 'status' => 401 ] );
	}

	if ( ! hash_equals( (string) TRINITY_RUNDOWN_TOKEN, trim( $m[1] ) ) ) {
		return new WP_Error( 'trun_bad_token', 'Invalid token.', [ 'status' => 403 ] );
	}

	return true;
}

function trun_rest_health(): WP_REST_Response {
	global $wpdb;

	$table  = TRUN_Storage::table();
	$exists = (bool) $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) );

	return new WP_REST_Response(
		[
			'ok'          => $exists,
			'plugin'      => TRUN_VERSION,
			'db_version'  => get_option( 'trun_db_version' ),
			'table'       => $table,
			'table_ready' => $exists,
		],
		$exists ? 200 : 500
	);
}

/**
 * Ingest one week's payload.
 *
 * Writes only stats_json, sort_order, and (write-once) opening_line. Any
 * editorial column present on an existing row is left untouched.
 */
function trun_rest_ingest_week( WP_REST_Request $request ) {
	$season = (int) $request->get_param( 'season' );
	$week   = (int) $request->get_param( 'week' );
	$games  = (array) $request->get_param( 'games' );

	if ( empty( $games ) ) {
		return new WP_Error( 'trun_empty', 'Payload contained no games.', [ 'status' => 422 ] );
	}

	$result = [
		'inserted'    => 0,
		'updated'     => 0,
		'openers_set' => 0,
		'skipped'     => [],
	];

	foreach ( $games as $i => $game ) {
		if ( ! is_array( $game ) || empty( $game['game_id'] ) ) {
			$result['skipped'][] = [
				'index'  => $i,
				'reason' => 'missing game_id',
			];
			continue;
		}

		$game['sort_order'] = $game['sort_order'] ?? $i;

		$action = TRUN_Storage::upsert_stats( $season, $week, $game );
		++$result[ $action ];

		// The opener is recorded from whatever the first successful run of the
		// week happened to see, and never revised afterwards.
		if ( ! empty( $game['odds'] ) && is_array( $game['odds'] ) ) {
			$opener = trun_extract_opener( $game['odds'] );
			if ( $opener && TRUN_Storage::set_opening_line_once( $season, $week, $game['game_id'], $opener ) ) {
				++$result['openers_set'];
			}
		}
	}

	if ( $result['skipped'] ) {
		trun_log( sprintf( 'Ingest %d wk%d skipped %d games.', $season, $week, count( $result['skipped'] ) ) );
	}

	return new WP_REST_Response( $result, 200 );
}

/**
 * Pull just the line-defining fields out of an odds block.
 *
 * Kept narrow on purpose: the opener is for showing movement, so it stores the
 * numbers that move and not the whole odds object.
 */
function trun_extract_opener( array $odds ): ?array {
	$keys   = [ 'spread', 'spread_favorite', 'total', 'home_moneyline', 'away_moneyline' ];
	$opener = [];

	foreach ( $keys as $key ) {
		if ( isset( $odds[ $key ] ) && null !== $odds[ $key ] ) {
			$opener[ $key ] = $odds[ $key ];
		}
	}

	if ( ! isset( $opener['spread'] ) && ! isset( $opener['total'] ) ) {
		return null;
	}

	$opener['captured_at'] = current_time( 'mysql', true );

	return $opener;
}

function trun_log( string $message ): void {
	if ( defined( 'WP_DEBUG' ) && WP_DEBUG ) {
		error_log( '[trinity-rundown] ' . $message );
	}
}
