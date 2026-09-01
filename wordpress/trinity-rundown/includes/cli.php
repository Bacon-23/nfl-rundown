<?php
/**
 * WP-CLI commands.
 *
 * These exist so plugin work never blocks on the pipeline: `wp rundown seed`
 * loads a fixture straight into the table, and `wp rundown publish` exercises
 * the freeze path without wp-admin.
 */

defined( 'ABSPATH' ) || exit;

class TRUN_CLI {

	/**
	 * Load a week payload from a JSON file.
	 *
	 * ## OPTIONS
	 *
	 * --file=<path>
	 * : Path to a payload JSON file, in the same shape the pipeline POSTs.
	 *
	 * ## EXAMPLES
	 *
	 *     wp rundown seed --file=fixtures/2026-week-01.json
	 *
	 * @when after_wp_load
	 */
	public function seed( $args, $assoc_args ): void {
		$path = $assoc_args['file'] ?? '';

		if ( ! $path || ! is_readable( $path ) ) {
			WP_CLI::error( "Cannot read file: {$path}" );
		}

		$payload = json_decode( (string) file_get_contents( $path ), true );
		if ( ! is_array( $payload ) ) {
			WP_CLI::error( 'File is not valid JSON.' );
		}

		foreach ( [ 'season', 'week', 'games' ] as $key ) {
			if ( ! isset( $payload[ $key ] ) ) {
				WP_CLI::error( "Payload is missing '{$key}'." );
			}
		}

		$season = (int) $payload['season'];
		$week   = (int) $payload['week'];
		$counts = [ 'inserted' => 0, 'updated' => 0, 'openers' => 0 ];

		foreach ( (array) $payload['games'] as $i => $game ) {
			if ( empty( $game['game_id'] ) ) {
				WP_CLI::warning( "Game at index {$i} has no game_id; skipped." );
				continue;
			}
			$game['sort_order'] = $game['sort_order'] ?? $i;

			$action = TRUN_Storage::upsert_stats( $season, $week, $game );
			$counts[ $action ]++;

			if ( ! empty( $game['odds'] ) && is_array( $game['odds'] ) ) {
				$opener = trun_extract_opener( $game['odds'] );
				if ( $opener && TRUN_Storage::set_opening_line_once( $season, $week, $game['game_id'], $opener ) ) {
					$counts['openers']++;
				}
			}
		}

		WP_CLI::success(
			sprintf(
				'%d wk%d: %d inserted, %d updated, %d openers recorded.',
				$season,
				$week,
				$counts['inserted'],
				$counts['updated'],
				$counts['openers']
			)
		);
	}

	/**
	 * Freeze a week's numbers.
	 *
	 * ## OPTIONS
	 *
	 * --season=<season>
	 * --week=<week>
	 *
	 * @when after_wp_load
	 */
	public function publish( $args, $assoc_args ): void {
		$season = (int) ( $assoc_args['season'] ?? 0 );
		$week   = (int) ( $assoc_args['week'] ?? 0 );

		if ( ! $season || ! $week ) {
			WP_CLI::error( 'Both --season and --week are required.' );
		}

		$frozen = TRUN_Storage::publish_week( $season, $week );
		WP_CLI::success( "Froze {$frozen} games for {$season} week {$week}." );
	}

	/**
	 * Unfreeze a week so refreshes flow through again.
	 *
	 * ## OPTIONS
	 *
	 * --season=<season>
	 * --week=<week>
	 *
	 * @when after_wp_load
	 */
	public function unlock( $args, $assoc_args ): void {
		$season = (int) ( $assoc_args['season'] ?? 0 );
		$week   = (int) ( $assoc_args['week'] ?? 0 );

		if ( ! $season || ! $week ) {
			WP_CLI::error( 'Both --season and --week are required.' );
		}

		TRUN_Storage::unlock_week( $season, $week );
		WP_CLI::success( "Unlocked {$season} week {$week}." );
	}

	/**
	 * Show what is stored for a week.
	 *
	 * ## OPTIONS
	 *
	 * --season=<season>
	 * --week=<week>
	 *
	 * @when after_wp_load
	 */
	public function status( $args, $assoc_args ): void {
		$season = (int) ( $assoc_args['season'] ?? 0 );
		$week   = (int) ( $assoc_args['week'] ?? 0 );

		$rows  = TRUN_Storage::get_week( $season, $week );
		$table = [];

		foreach ( $rows as $row ) {
			$notes = json_decode( (string) $row->notes_json, true );
			$table[] = [
				'game_id'    => $row->game_id,
				'locked'     => $row->locked ? 'yes' : 'no',
				'opener'     => $row->opening_line ? 'yes' : 'no',
				'notes'      => is_array( $notes ) && array_filter( $notes ) ? 'yes' : 'no',
				'updated_at' => $row->updated_at,
			];
		}

		if ( ! $table ) {
			WP_CLI::warning( "Nothing stored for {$season} week {$week}." );
			return;
		}

		WP_CLI\Utils\format_items( 'table', $table, [ 'game_id', 'locked', 'opener', 'notes', 'updated_at' ] );
	}
}

WP_CLI::add_command( 'rundown', 'TRUN_CLI' );
