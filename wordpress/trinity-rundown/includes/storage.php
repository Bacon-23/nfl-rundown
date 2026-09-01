<?php
/**
 * Persistence for weekly matchup rows.
 *
 * The table deliberately splits pipeline-owned data from human-owned data:
 *
 *   stats_json      pipeline writes freely, every run
 *   overrides_json  human, per-field corrections layered over stats
 *   notes_json      human, editorial prose
 *   published_json  frozen copy of the merged stats taken at publish time
 *   opening_line    write-once, the first line seen in a given week
 *
 * No write path in this class lets the pipeline touch a human column, and
 * opening_line is guarded so a re-run cannot rewrite history.
 */

defined( 'ABSPATH' ) || exit;

class TRUN_Storage {

	const DB_VERSION = TRUN_VERSION;

	/**
	 * The one identifier in this file that cannot be a placeholder.
	 *
	 * $wpdb->prepare() substitutes values, not identifiers, so a table
	 * name has to reach the query as text. That makes phpcs flag every
	 * query below, and each carries a phpcs:ignore pointing here. The
	 * name is safe by construction: a core-controlled prefix and a
	 * literal suffix, with no caller input anywhere in it. Every actual
	 * value in those queries still goes through %s or %d.
	 */
	public static function table(): string {
		global $wpdb;
		return $wpdb->prefix . 'trinity_rundown_games';
	}

	public static function install(): void {
		global $wpdb;

		$table   = self::table();
		$charset = $wpdb->get_charset_collate();

		$sql = "CREATE TABLE {$table} (
			id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
			season         SMALLINT        NOT NULL,
			week           TINYINT         NOT NULL,
			game_id        VARCHAR(32)     NOT NULL,
			stats_json     LONGTEXT        NOT NULL,
			overrides_json LONGTEXT        NULL,
			notes_json     LONGTEXT        NULL,
			published_json LONGTEXT        NULL,
			opening_line   LONGTEXT        NULL,
			locked         TINYINT(1)      NOT NULL DEFAULT 0,
			sort_order     SMALLINT        NOT NULL DEFAULT 0,
			updated_at     DATETIME        NOT NULL,
			PRIMARY KEY  (id),
			UNIQUE KEY uq_game (season, week, game_id),
			KEY idx_week (season, week, sort_order)
		) {$charset};";

		require_once ABSPATH . 'wp-admin/includes/upgrade.php';
		dbDelta( $sql );

		update_option( 'trun_db_version', self::DB_VERSION );
	}

	/**
	 * Keys a refresh must not blank out by omitting them.
	 *
	 * The pipeline drops a key entirely when a source could not be read, which
	 * is a different claim from sending an empty value. An ESPN outage means
	 * "no injury data this run", not "nobody is hurt" -- so the previously
	 * stored table is kept rather than replaced with nothing.
	 *
	 * A key present but empty is honoured: that is the pipeline actively
	 * saying the list is empty.
	 */
	const STICKY_KEYS = [ 'injuries', 'weather', 'efficiency', 'passing', 'rushing', 'dvp' ];

	/**
	 * Upsert the pipeline-owned half of a game row.
	 *
	 * Editorial columns are never named in the ON DUPLICATE KEY UPDATE clause,
	 * so an existing row keeps its notes and overrides no matter how many
	 * times the pipeline runs.
	 *
	 * @param array $game One game object from the pipeline payload.
	 * @return string 'inserted' or 'updated'
	 */
	public static function upsert_stats( int $season, int $week, array $game ): string {
		global $wpdb;

		$table   = self::table();
		$game_id = $game['game_id'];
		$order   = isset( $game['sort_order'] ) ? (int) $game['sort_order'] : 0;
		$now     = current_time( 'mysql', true );

		$existing = self::get_game( $season, $week, $game_id );
		$game     = self::carry_forward( $game, $existing );
		$stats    = wp_json_encode( $game );

		if ( $existing && 1 === (int) $existing->locked ) {
			// Frozen at publish. Keep the row current so the admin screen can
			// show drift, but published_json is what actually renders.
			$wpdb->query(
				$wpdb->prepare(
					// phpcs:ignore WordPress.DB.PreparedSQL.InterpolatedNotPrepared -- $table is self::table(); see the note there.
					"UPDATE {$table} SET stats_json = %s, updated_at = %s
					 WHERE season = %d AND week = %d AND game_id = %s",
					$stats,
					$now,
					$season,
					$week,
					$game_id
				)
			);
			return 'updated';
		}

		$wpdb->query(
			$wpdb->prepare(
				// phpcs:ignore WordPress.DB.PreparedSQL.InterpolatedNotPrepared -- $table is self::table(); see the note there.
				"INSERT INTO {$table} (season, week, game_id, stats_json, sort_order, updated_at)
				 VALUES (%d, %d, %s, %s, %d, %s)
				 ON DUPLICATE KEY UPDATE
					stats_json = VALUES(stats_json),
					sort_order = VALUES(sort_order),
					updated_at = VALUES(updated_at)",
				$season,
				$week,
				$game_id,
				$stats,
				$order,
				$now
			)
		);

		return $existing ? 'updated' : 'inserted';
	}

	/**
	 * Preserve sticky keys the incoming payload left out.
	 *
	 * @param array       $game     The incoming game object.
	 * @param object|null $existing The stored row, if any.
	 */
	private static function carry_forward( array $game, ?object $existing ): array {
		if ( ! $existing ) {
			return $game;
		}

		$stored = json_decode( (string) $existing->stats_json, true );
		if ( ! is_array( $stored ) ) {
			return $game;
		}

		foreach ( self::STICKY_KEYS as $key ) {
			// array_key_exists, not isset: a key present with a null or empty
			// value is the pipeline speaking, and must win.
			if ( ! array_key_exists( $key, $game ) && array_key_exists( $key, $stored ) ) {
				$game[ $key ] = $stored[ $key ];
			}
		}

		return $game;
	}

	/**
	 * Record the opening line for a game, once and only once.
	 *
	 * Returns true only when this call is what set the value. A repeat call,
	 * or a call against a row that already has an opener, is a no-op.
	 */
	public static function set_opening_line_once( int $season, int $week, string $game_id, array $line ): bool {
		global $wpdb;

		$table = self::table();

		// The NULL guard lives in the WHERE clause so two concurrent pipeline
		// runs cannot race each other into overwriting an opener.
		$rows = $wpdb->query(
			$wpdb->prepare(
				// phpcs:ignore WordPress.DB.PreparedSQL.InterpolatedNotPrepared -- $table is self::table(); see the note there.
				"UPDATE {$table} SET opening_line = %s
				 WHERE season = %d AND week = %d AND game_id = %s AND opening_line IS NULL",
				wp_json_encode( $line ),
				$season,
				$week,
				$game_id
			)
		);

		return 1 === $rows;
	}

	/** Force-overwrite an opener. Only the --backfill-open repair path uses this. */
	public static function force_opening_line( int $season, int $week, string $game_id, array $line ): void {
		global $wpdb;
		$wpdb->update(
			self::table(),
			[ 'opening_line' => wp_json_encode( $line ) ],
			[
				'season'  => $season,
				'week'    => $week,
				'game_id' => $game_id,
			],
			[ '%s' ],
			[ '%d', '%d', '%s' ]
		);
	}

	public static function save_editorial( int $season, int $week, string $game_id, ?array $notes, ?array $overrides ): void {
		global $wpdb;

		$data   = [ 'updated_at' => current_time( 'mysql', true ) ];
		$format = [ '%s' ];

		if ( null !== $notes ) {
			$data['notes_json'] = wp_json_encode( $notes );
			$format[]           = '%s';
		}
		if ( null !== $overrides ) {
			$data['overrides_json'] = wp_json_encode( $overrides );
			$format[]               = '%s';
		}

		$wpdb->update(
			self::table(),
			$data,
			[
				'season'  => $season,
				'week'    => $week,
				'game_id' => $game_id,
			],
			$format,
			[ '%d', '%d', '%s' ]
		);
	}

	public static function get_game( int $season, int $week, string $game_id ): ?object {
		global $wpdb;
		$row = $wpdb->get_row(
			$wpdb->prepare(
				// phpcs:ignore WordPress.DB.PreparedSQL.NotPrepared -- table name only; see the note on self::table().
				'SELECT * FROM ' . self::table() . ' WHERE season = %d AND week = %d AND game_id = %s',
				$season,
				$week,
				$game_id
			)
		);
		return $row ? $row : null;
	}

	/** @return object[] */
	public static function get_week( int $season, int $week ): array {
		global $wpdb;
		return $wpdb->get_results(
			$wpdb->prepare(
				// phpcs:ignore WordPress.DB.PreparedSQL.NotPrepared -- table name only; see the note on self::table().
				'SELECT * FROM ' . self::table() . ' WHERE season = %d AND week = %d ORDER BY sort_order ASC, game_id ASC',
				$season,
				$week
			)
		);
	}

	/**
	 * Freeze a week: snapshot the merged view into published_json and lock it.
	 *
	 * @return int Number of games frozen.
	 */
	public static function publish_week( int $season, int $week ): int {
		global $wpdb;

		$frozen = 0;
		foreach ( self::get_week( $season, $week ) as $row ) {
			$merged = self::merge_row( $row );
			$wpdb->update(
				self::table(),
				[
					'published_json' => wp_json_encode( $merged ),
					'locked'         => 1,
					'updated_at'     => current_time( 'mysql', true ),
				],
				[ 'id' => $row->id ],
				[ '%s', '%d', '%s' ],
				[ '%d' ]
			);
			++$frozen;
		}
		return $frozen;
	}

	public static function unlock_week( int $season, int $week ): void {
		global $wpdb;
		$wpdb->query(
			$wpdb->prepare(
				// phpcs:ignore WordPress.DB.PreparedSQL.NotPrepared -- table name only; see the note on self::table().
				'UPDATE ' . self::table() . ' SET locked = 0 WHERE season = %d AND week = %d',
				$season,
				$week
			)
		);
	}

	/**
	 * Build the view a renderer should use: stats, then human overrides, then
	 * notes, then the opening line. Later layers win.
	 */
	public static function merge_row( object $row ): array {
		$stats     = json_decode( (string) $row->stats_json, true );
		$overrides = json_decode( (string) $row->overrides_json, true );
		$notes     = json_decode( (string) $row->notes_json, true );
		$opening   = json_decode( (string) $row->opening_line, true );

		$stats     = is_array( $stats ) ? $stats : [];
		$overrides = is_array( $overrides ) ? $overrides : [];
		$notes     = is_array( $notes ) ? $notes : [];

		$merged = self::deep_merge( $stats, $overrides );

		if ( $notes ) {
			$merged['notes'] = $notes;
		}
		if ( is_array( $opening ) ) {
			if ( ! isset( $merged['odds'] ) || ! is_array( $merged['odds'] ) ) {
				$merged['odds'] = [];
			}
			$merged['odds']['opening'] = $opening;
		}

		$merged['_meta'] = [
			'locked'     => (bool) $row->locked,
			'updated_at' => $row->updated_at,
		];

		return $merged;
	}

	/** The view to render: the frozen snapshot when locked, otherwise live. */
	public static function view_row( object $row ): array {
		if ( 1 === (int) $row->locked && $row->published_json ) {
			$published = json_decode( (string) $row->published_json, true );
			if ( is_array( $published ) ) {
				return $published;
			}
		}
		return self::merge_row( $row );
	}

	/**
	 * Recursive merge where an override value replaces the base value.
	 *
	 * Differs from array_merge_recursive (which turns colliding scalars into
	 * arrays) and from array_replace_recursive (which merges list elements
	 * positionally). Lists here are replaced wholesale, so overriding a
	 * three-row injury table does not leave a stale fourth row behind.
	 */
	private static function deep_merge( array $base, array $over ): array {
		foreach ( $over as $key => $value ) {
			$recurse = is_array( $value )
				&& isset( $base[ $key ] )
				&& is_array( $base[ $key ] )
				&& ! array_is_list( $value );

			$base[ $key ] = $recurse ? self::deep_merge( $base[ $key ], $value ) : $value;
		}
		return $base;
	}
}
