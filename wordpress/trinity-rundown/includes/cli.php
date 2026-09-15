<?php
/**
 * WP-CLI commands.
 *
 * These exist so plugin work never blocks on the pipeline: `wp rundown seed`
 * loads a fixture straight into the table, and `wp rundown publish` exercises
 * the freeze path without wp-admin. `wp rundown reopen` is the one repair path
 * for a wrong opener, the only value a re-run cannot fix.
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
		$payload = $this->read_payload( $assoc_args );

		$season = (int) $payload['season'];
		$week   = (int) $payload['week'];
		$counts = [
			'inserted' => 0,
			'updated'  => 0,
			'openers'  => 0,
		];

		foreach ( (array) $payload['games'] as $i => $game ) {
			if ( empty( $game['game_id'] ) ) {
				WP_CLI::warning( "Game at index {$i} has no game_id; skipped." );
				continue;
			}
			$game['sort_order'] = $game['sort_order'] ?? $i;

			$action = TRUN_Storage::upsert_stats( $season, $week, $game );
			++$counts[ $action ];

			if ( ! empty( $game['odds'] ) && is_array( $game['odds'] ) ) {
				$opener = trun_extract_opener( $game['odds'] );
				if ( $opener && TRUN_Storage::set_opening_line_once( $season, $week, $game['game_id'], $opener ) ) {
					++$counts['openers'];
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
	 * Overwrite recorded openers from a payload, bypassing the write-once guard.
	 *
	 * The repair path for a bad opener -- in practice one an unattended build
	 * froze from nflverse fallback lines. Reads a payload in the shape the
	 * pipeline builds, so a dry-run artifact from the build workflow is the
	 * natural source, and writes opening_line only: stats, editorial, and lock
	 * state are left alone.
	 *
	 * A game is skipped rather than forced when its odds in the file are not
	 * from the book, when the file has no line for it, or when no row is
	 * stored. Shows each change and asks before writing anything.
	 *
	 * ## OPTIONS
	 *
	 * --file=<path>
	 * : Path to a payload JSON file, in the same shape the pipeline POSTs.
	 *
	 * [--game=<game_id>]
	 * : Force only this game. Without it, every game in the file.
	 *
	 * [--yes]
	 * : Skip the confirmation prompt.
	 *
	 * ## EXAMPLES
	 *
	 *     wp rundown reopen --file=2026-week-03.json --game=2026_03_KC_BUF
	 *
	 * @when after_wp_load
	 */
	public function reopen( $args, $assoc_args ): void {
		$payload = $this->read_payload( $assoc_args );

		$season   = (int) $payload['season'];
		$week     = (int) $payload['week'];
		$only     = (string) ( $assoc_args['game'] ?? '' );
		$captured = $this->payload_time( $payload );

		$plan      = [];
		$locked    = 0;
		$skipped   = 0;
		$unchanged = 0;
		$found     = false;

		foreach ( (array) $payload['games'] as $game ) {
			$game_id = is_array( $game ) ? (string) ( $game['game_id'] ?? '' ) : '';
			if ( '' === $game_id || ( '' !== $only && $only !== $game_id ) ) {
				continue;
			}
			$found = true;

			// The whole point of forcing is to replace a fallback line with the
			// book's, so a fallback line is never an acceptable replacement.
			$odds   = isset( $game['odds'] ) && is_array( $game['odds'] ) ? $game['odds'] : [];
			$source = (string) ( $odds['source'] ?? '' );
			if ( 'odds_api' !== $source ) {
				WP_CLI::warning( "{$game_id}: odds source is '{$source}', not odds_api; skipped." );
				++$skipped;
				continue;
			}

			$opener = trun_extract_opener( $odds );
			if ( ! $opener ) {
				WP_CLI::warning( "{$game_id}: no spread or total in the file; skipped." );
				++$skipped;
				continue;
			}

			$row = TRUN_Storage::get_game( $season, $week, $game_id );
			if ( ! $row ) {
				WP_CLI::warning( "{$game_id}: nothing stored for {$season} week {$week}; skipped." );
				++$skipped;
				continue;
			}

			// When the line was seen, not when it was repaired.
			if ( $captured ) {
				$opener['captured_at'] = $captured;
			}

			$current = json_decode( (string) $row->opening_line, true );
			$current = is_array( $current ) ? $current : null;

			if ( $current && $this->same_line( $current, $opener ) ) {
				++$unchanged;
				continue;
			}

			$plan[ $game_id ] = $opener;
			if ( 1 === (int) $row->locked ) {
				++$locked;
			}

			WP_CLI::log( sprintf( '%-18s %s  ->  %s', $game_id, $this->describe_line( $current ), $this->describe_line( $opener ) ) );
		}

		if ( '' !== $only && ! $found ) {
			WP_CLI::error( "Game {$only} is not in the file." );
		}

		if ( ! $plan ) {
			WP_CLI::success( "Nothing to force: 0 forced, {$skipped} skipped, {$unchanged} unchanged." );
			return;
		}

		if ( $locked ) {
			WP_CLI::warning( "{$locked} of these games are in a published week. Readers see the frozen snapshot, so a corrected opener shows only after `wp rundown unlock` and a fresh publish." );
		}

		WP_CLI::confirm( sprintf( 'Force %d opener(s) for %d week %d?', count( $plan ), $season, $week ), $assoc_args );

		$forced = 0;
		foreach ( $plan as $game_id => $opener ) {
			if ( TRUN_Storage::force_opening_line( $season, $week, $game_id, $opener ) ) {
				++$forced;
			} else {
				WP_CLI::warning( "{$game_id}: write failed; opener unchanged." );
			}
		}

		$summary = "{$forced} forced, {$skipped} skipped, {$unchanged} unchanged.";
		if ( count( $plan ) !== $forced ) {
			WP_CLI::error( $summary );
		}

		WP_CLI::success( $summary );
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
			$notes   = json_decode( (string) $row->notes_json, true );
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

	/**
	 * Read and minimally validate a payload file named by --file.
	 *
	 * Private, so WP-CLI does not register it as a subcommand.
	 */
	private function read_payload( array $assoc_args ): array {
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

		return $payload;
	}

	/** The payload's generated_at in the column's MySQL format, or null. */
	private function payload_time( array $payload ): ?string {
		$stamp = strtotime( (string) ( $payload['generated_at'] ?? '' ) );
		return $stamp ? gmdate( 'Y-m-d H:i:s', $stamp ) : null;
	}

	/** Whether two openers carry the same line, ignoring when each was seen. */
	private function same_line( array $a, array $b ): bool {
		$normalise = static function ( array $line ): array {
			unset( $line['captured_at'] );
			// JSON round-trips -3.0 as -3, and those are the same line.
			foreach ( $line as $key => $value ) {
				if ( is_int( $value ) || is_float( $value ) ) {
					$line[ $key ] = (float) $value;
				}
			}
			ksort( $line );
			return $line;
		};

		return $normalise( $a ) === $normalise( $b );
	}

	/** An opener as a reader would say it: "KC -3 o/u 50.5". */
	private function describe_line( ?array $line ): string {
		if ( ! $line ) {
			return 'none';
		}

		$parts = [];
		if ( isset( $line['spread'] ) ) {
			$parts[] = trim( ( $line['spread_favorite'] ?? '' ) . ' ' . (float) $line['spread'] );
		}
		if ( isset( $line['total'] ) ) {
			$parts[] = 'o/u ' . (float) $line['total'];
		}

		return implode( ' ', $parts );
	}
}

WP_CLI::add_command( 'rundown', 'TRUN_CLI' );
