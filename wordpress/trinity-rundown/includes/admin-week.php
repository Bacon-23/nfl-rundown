<?php
/**
 * The writer's screen: every game in a week on one page.
 *
 * This is the only place a human writes to the database. It reads the merged
 * view so the writer sees exactly what the page will show, and it writes only
 * notes_json and overrides_json -- the two columns the pipeline never touches.
 *
 * Publishing here freezes the numbers and nothing else. It does not create or
 * edit a post: the REST route was deliberately denied post-creation powers and
 * this screen inherits that restraint. The writer makes the weekly post and
 * pastes [rundown_week] into it.
 *
 * No REST, no admin-ajax, no build step. Forms POST to admin-post.php, the
 * handler redirects, and the result arrives as a notice in the query string.
 */

defined( 'ABSPATH' ) || exit;

const TRUN_ADMIN_SLUG = 'trinity-rundown';
const TRUN_ADMIN_CAP  = 'edit_posts';

/**
 * The editorial sections, in the order the writer fills them in.
 *
 * These keys are the contract with trun_render_notes() in render.php. Renaming
 * one here without renaming it there silently stops that section rendering.
 */
function trun_admin_note_fields(): array {
	return [
		'scouting'   => [
			'label' => __( 'Scouting Notes', 'trinity-rundown' ),
			'rows'  => 6,
		],
		'td_leans'   => [
			'label' => __( 'Anytime TD Leans', 'trinity-rundown' ),
			'rows'  => 3,
		],
		'prediction' => [
			'label' => __( 'Score Prediction', 'trinity-rundown' ),
			'rows'  => 2,
		],
	];
}

/**
 * Per-field corrections the writer can layer over the pipeline's numbers.
 *
 * Only fields that render today are listed; the stat tables get their own
 * entries when Phase 2 lands. `path` is the dot-path into the payload, which
 * is where storage's deep_merge() applies the value. The form key is a flat
 * slug rather than the path itself, so nothing depends on how PHP parses a
 * dot inside a field name.
 */
function trun_admin_override_fields(): array {
	return [
		'spread'          => [
			'path'  => 'odds.spread',
			'label' => __( 'Spread', 'trinity-rundown' ),
			'type'  => 'number',
			'hint'  => __( "Negative, read from the favorite's side: -4.5", 'trinity-rundown' ),
		],
		'spread_favorite' => [
			'path'  => 'odds.spread_favorite',
			'label' => __( 'Favorite', 'trinity-rundown' ),
			'type'  => 'text',
			'hint'  => __( 'Team abbreviation, e.g. SEA', 'trinity-rundown' ),
		],
		'total'           => [
			'path'  => 'odds.total',
			'label' => __( 'Total', 'trinity-rundown' ),
			'type'  => 'number',
			'hint'  => '',
		],
		'away_team_total' => [
			'path'  => 'odds.away_team_total',
			'label' => __( 'Away team total', 'trinity-rundown' ),
			'type'  => 'number',
			'hint'  => '',
		],
		'home_team_total' => [
			'path'  => 'odds.home_team_total',
			'label' => __( 'Home team total', 'trinity-rundown' ),
			'type'  => 'number',
			'hint'  => '',
		],
		'weather'         => [
			'path'  => 'weather.summary',
			'label' => __( 'Weather', 'trinity-rundown' ),
			'type'  => 'text',
			'hint'  => __( 'Replaces the forecast line, e.g. "Indoors, no weather factor"', 'trinity-rundown' ),
		],
	];
}

/* -------------------------------------------------------------------------
 * Menu and assets
 * ---------------------------------------------------------------------- */

add_action( 'admin_menu', 'trun_admin_menu' );

function trun_admin_menu(): void {
	add_menu_page(
		__( 'Rundown', 'trinity-rundown' ),
		__( 'Rundown', 'trinity-rundown' ),
		TRUN_ADMIN_CAP,
		TRUN_ADMIN_SLUG,
		'trun_admin_render_page',
		'dashicons-clipboard',
		26
	);
}

add_action( 'admin_enqueue_scripts', 'trun_admin_assets' );

function trun_admin_assets( string $hook ): void {
	if ( 'toplevel_page_' . TRUN_ADMIN_SLUG !== $hook ) {
		return;
	}

	wp_enqueue_style( 'trinity-rundown-admin', TRUN_URL . 'assets/admin.css', [], TRUN_VERSION );
	wp_enqueue_script(
		'trinity-rundown-admin',
		TRUN_URL . 'assets/admin.js',
		[],
		TRUN_VERSION,
		[
			'strategy'  => 'defer',
			'in_footer' => true,
		]
	);
}

/* -------------------------------------------------------------------------
 * Which week is on screen
 * ---------------------------------------------------------------------- */

/**
 * Resolve the selected week from the URL, falling back to the newest stored.
 *
 * One parameter carries both halves -- `sw=2026-1` -- so the picker, the
 * redirects and any bookmarked URL all agree on a single canonical form.
 *
 * @param object[] $weeks Rows from TRUN_Storage::list_weeks().
 * @return array{0:int,1:int} Season and week, both 0 when nothing is stored.
 */
function trun_admin_selected_week( array $weeks ): array {
	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- read-only navigation, validated against the stored weeks below.
	$raw = isset( $_GET['sw'] ) ? sanitize_text_field( wp_unslash( $_GET['sw'] ) ) : '';

	if ( preg_match( '/^(\d{4})-(\d{1,2})$/', $raw, $m ) ) {
		$season = (int) $m[1];
		$week   = (int) $m[2];

		foreach ( $weeks as $candidate ) {
			if ( (int) $candidate->season === $season && (int) $candidate->week === $week ) {
				return [ $season, $week ];
			}
		}
	}

	if ( $weeks ) {
		return [ (int) $weeks[0]->season, (int) $weeks[0]->week ];
	}

	return [ 0, 0 ];
}

function trun_admin_week_key( int $season, int $week ): string {
	return $season . '-' . $week;
}

function trun_admin_url( int $season, int $week, array $extra = [] ): string {
	return add_query_arg(
		array_merge(
			[
				'page' => TRUN_ADMIN_SLUG,
				'sw'   => trun_admin_week_key( $season, $week ),
			],
			$extra
		),
		admin_url( 'admin.php' )
	);
}

/* -------------------------------------------------------------------------
 * The page
 * ---------------------------------------------------------------------- */

function trun_admin_render_page(): void {
	if ( ! current_user_can( TRUN_ADMIN_CAP ) ) {
		wp_die( esc_html__( 'You are not allowed to edit the Rundown.', 'trinity-rundown' ), 403 );
	}

	$weeks = TRUN_Storage::list_weeks();

	list( $season, $week ) = trun_admin_selected_week( $weeks );

	$rows   = $season ? TRUN_Storage::get_week( $season, $week ) : [];
	$locked = trun_admin_week_is_locked( $rows );

	echo '<div class="wrap trun-adm">';
	echo '<h1 class="wp-heading-inline">' . esc_html__( 'Rundown', 'trinity-rundown' ) . '</h1>';

	trun_admin_render_notice();

	if ( ! $weeks ) {
		echo '<div class="notice notice-info inline"><p>'
			. esc_html__( 'No weeks are stored yet. Run the pipeline, then reload this page.', 'trinity-rundown' )
			. '</p></div></div>';
		return;
	}

	trun_admin_render_picker( $weeks, $season, $week );
	trun_admin_render_week_actions( $season, $week, $locked );

	if ( ! $rows ) {
		echo '<div class="notice notice-warning inline"><p>'
			. esc_html__( 'This week has no games stored.', 'trinity-rundown' )
			. '</p></div></div>';
		return;
	}

	?>
	<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" class="trun-adm__form">
		<?php wp_nonce_field( 'trun_save_week' ); ?>
		<input type="hidden" name="action" value="trun_save_week" />
		<input type="hidden" name="season" value="<?php echo esc_attr( (string) $season ); ?>" />
		<input type="hidden" name="week" value="<?php echo esc_attr( (string) $week ); ?>" />

		<?php foreach ( $rows as $row ) : ?>
			<?php trun_admin_render_game( $row ); ?>
		<?php endforeach; ?>

		<p class="trun-adm__submit">
			<button type="submit" class="button button-primary button-hero">
				<?php esc_html_e( 'Save all games', 'trinity-rundown' ); ?>
			</button>
		</p>
	</form>
	</div>
	<?php
}

/**
 * Week picker. A plain GET form, so every week has a linkable URL.
 *
 * @param object[] $weeks Rows from TRUN_Storage::list_weeks().
 */
function trun_admin_render_picker( array $weeks, int $season, int $week ): void {
	?>
	<form method="get" action="<?php echo esc_url( admin_url( 'admin.php' ) ); ?>" class="trun-adm__picker">
		<input type="hidden" name="page" value="<?php echo esc_attr( TRUN_ADMIN_SLUG ); ?>" />
		<label for="trun-sw"><?php esc_html_e( 'Week', 'trinity-rundown' ); ?></label>
		<select name="sw" id="trun-sw">
			<?php foreach ( $weeks as $candidate ) : ?>
				<?php $key = trun_admin_week_key( (int) $candidate->season, (int) $candidate->week ); ?>
				<option value="<?php echo esc_attr( $key ); ?>" <?php selected( $key, trun_admin_week_key( $season, $week ) ); ?>>
					<?php
					printf(
						/* translators: 1: season year, 2: week number, 3: number of games stored. */
						esc_html__( '%1$d week %2$d (%3$d games)', 'trinity-rundown' ),
						(int) $candidate->season,
						(int) $candidate->week,
						(int) $candidate->games
					);
					?>
				</option>
			<?php endforeach; ?>
		</select>
		<button type="submit" class="button"><?php esc_html_e( 'Go', 'trinity-rundown' ); ?></button>
	</form>
	<?php
}

/**
 * The Publish / Unlock strip.
 *
 * Both live outside the editorial form and carry their own nonces, so no path
 * through Save can freeze or thaw a week by accident.
 */
function trun_admin_render_week_actions( int $season, int $week, bool $locked ): void {
	?>
	<div class="trun-adm__actions">
		<p class="trun-adm__state">
			<?php if ( $locked ) : ?>
				<span class="trun-adm__badge trun-adm__badge--locked"><?php esc_html_e( 'Published', 'trinity-rundown' ); ?></span>
				<?php esc_html_e( 'The page renders the frozen snapshot. Pipeline refreshes no longer change it.', 'trinity-rundown' ); ?>
			<?php else : ?>
				<span class="trun-adm__badge"><?php esc_html_e( 'Live', 'trinity-rundown' ); ?></span>
				<?php esc_html_e( 'The page follows the pipeline. Publish to freeze the numbers.', 'trinity-rundown' ); ?>
			<?php endif; ?>
		</p>

		<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
			<?php wp_nonce_field( 'trun_publish_week' ); ?>
			<input type="hidden" name="action" value="trun_publish_week" />
			<input type="hidden" name="season" value="<?php echo esc_attr( (string) $season ); ?>" />
			<input type="hidden" name="week" value="<?php echo esc_attr( (string) $week ); ?>" />
			<button type="submit" class="button button-primary"
				data-trun-confirm="<?php esc_attr_e( 'Freeze this week? The published page stops following the pipeline until you unlock it.', 'trinity-rundown' ); ?>">
				<?php
				if ( $locked ) {
					esc_html_e( 'Re-publish (refreeze)', 'trinity-rundown' );
				} else {
					esc_html_e( 'Publish & freeze', 'trinity-rundown' );
				}
				?>
			</button>
		</form>

		<?php if ( $locked ) : ?>
			<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
				<?php wp_nonce_field( 'trun_unlock_week' ); ?>
				<input type="hidden" name="action" value="trun_unlock_week" />
				<input type="hidden" name="season" value="<?php echo esc_attr( (string) $season ); ?>" />
				<input type="hidden" name="week" value="<?php echo esc_attr( (string) $week ); ?>" />
				<button type="submit" class="button"
					data-trun-confirm="<?php esc_attr_e( 'Unlock this week? The published page goes back to following the pipeline.', 'trinity-rundown' ); ?>">
					<?php esc_html_e( 'Unlock', 'trinity-rundown' ); ?>
				</button>
			</form>
		<?php endif; ?>
	</div>
	<?php
}

/**
 * One game: what the pipeline says, what is wrong with it, and the boxes.
 */
function trun_admin_render_game( object $row ): void {
	$game      = TRUN_Storage::merge_row( $row );
	$stats     = json_decode( (string) $row->stats_json, true );
	$overrides = json_decode( (string) $row->overrides_json, true );
	$notes     = json_decode( (string) $row->notes_json, true );

	$stats     = is_array( $stats ) ? $stats : [];
	$overrides = is_array( $overrides ) ? $overrides : [];
	$notes     = is_array( $notes ) ? $notes : [];

	$game_id = (string) $row->game_id;

	?>
	<section class="trun-adm__game">
		<header class="trun-adm__head">
			<h2 class="trun-adm__title"><?php echo esc_html( trun_matchup_label( $game ) ); ?></h2>
			<p class="trun-adm__meta">
				<?php echo esc_html( trun_get( $game, 'kickoff.display', 'TBD' ) ); ?>
				<span class="trun-adm__sep">&middot;</span>
				<code><?php echo esc_html( $game_id ); ?></code>
			</p>
		</header>

		<?php trun_admin_render_flags( $row, $game ); ?>
		<?php trun_admin_render_readout( $game ); ?>

		<div class="trun-adm__notes">
			<?php foreach ( trun_admin_note_fields() as $key => $spec ) : ?>
				<?php $field_id = trun_admin_field_id( $game_id, 'notes', $key ); ?>
				<p class="trun-adm__field">
					<label for="<?php echo esc_attr( $field_id ); ?>"><?php echo esc_html( $spec['label'] ); ?></label>
					<textarea
						id="<?php echo esc_attr( $field_id ); ?>"
						name="<?php echo esc_attr( trun_admin_field_name( $game_id, 'notes', $key ) ); ?>"
						rows="<?php echo esc_attr( (string) $spec['rows'] ); ?>"
						class="large-text"><?php echo esc_textarea( (string) ( $notes[ $key ] ?? '' ) ); ?></textarea>
				</p>
			<?php endforeach; ?>
		</div>

		<details class="trun-adm__overrides"<?php echo $overrides ? ' open' : ''; ?>>
			<summary><?php esc_html_e( 'Corrections', 'trinity-rundown' ); ?></summary>
			<p class="trun-adm__hint">
				<?php esc_html_e( 'Leave a box empty to use the pipeline value shown in grey. Clearing a box removes the correction.', 'trinity-rundown' ); ?>
			</p>
			<div class="trun-adm__grid">
				<?php foreach ( trun_admin_override_fields() as $key => $spec ) : ?>
					<?php
					$field_id    = trun_admin_field_id( $game_id, 'over', $key );
					$stored      = trun_get( $overrides, $spec['path'], '' );
					$pipeline    = trun_get( $stats, $spec['path'], '' );
					$placeholder = '' === (string) $pipeline ? __( 'not set', 'trinity-rundown' ) : (string) $pipeline;
					$is_number   = 'number' === $spec['type'];
					?>
					<p class="trun-adm__field">
						<label for="<?php echo esc_attr( $field_id ); ?>"><?php echo esc_html( $spec['label'] ); ?></label>
						<input
							type="<?php echo $is_number ? 'number' : 'text'; ?>"
							<?php echo $is_number ? 'step="any"' : ''; ?>
							id="<?php echo esc_attr( $field_id ); ?>"
							name="<?php echo esc_attr( trun_admin_field_name( $game_id, 'over', $key ) ); ?>"
							value="<?php echo esc_attr( (string) $stored ); ?>"
							placeholder="<?php echo esc_attr( $placeholder ); ?>" />
						<?php if ( $spec['hint'] ) : ?>
							<span class="trun-adm__hint"><?php echo esc_html( $spec['hint'] ); ?></span>
						<?php endif; ?>
					</p>
				<?php endforeach; ?>
			</div>
		</details>
	</section>
	<?php
}

/**
 * The numbers as they stand, so the writer can write against them.
 */
function trun_admin_render_readout( array $game ): void {
	$away = (string) trun_get( $game, 'away.abbr', 'AWAY' );
	$home = (string) trun_get( $game, 'home.abbr', 'HOME' );

	$cells = [
		[ __( 'Spread', 'trinity-rundown' ), trun_spread_text( $game ) ],
		[ __( 'Total', 'trinity-rundown' ), (string) trun_get( $game, 'odds.total', '--' ) ],
		[ $away . ' ' . __( 'total', 'trinity-rundown' ), (string) trun_get( $game, 'odds.away_team_total', '--' ) ],
		[ $home . ' ' . __( 'total', 'trinity-rundown' ), (string) trun_get( $game, 'odds.home_team_total', '--' ) ],
		[ __( 'Weather', 'trinity-rundown' ), (string) trun_get( $game, 'weather.summary', 'TBD' ) ],
		[ __( 'Injuries listed', 'trinity-rundown' ), (string) count( (array) trun_get( $game, 'injuries', [] ) ) ],
	];

	?>
	<dl class="trun-adm__readout">
		<?php foreach ( $cells as $cell ) : ?>
			<div class="trun-adm__cell">
				<dt><?php echo esc_html( $cell[0] ); ?></dt>
				<dd><?php echo esc_html( '' === $cell[1] ? '--' : $cell[1] ); ?></dd>
			</div>
		<?php endforeach; ?>
	</dl>
	<?php
}

/**
 * Everything the writer should know before publishing this game.
 *
 * The payload carries `odds.source` and `odds.team_totals_derived` precisely so
 * they can be surfaced here; publishing a number nobody can defend is the main
 * reputational risk in the whole build.
 */
function trun_admin_render_flags( object $row, array $game ): void {
	$flags = [];

	if ( 'nflverse_fallback' === trun_get( $game, 'odds.source', '' ) ) {
		$flags[] = sprintf(
			/* translators: %s: the sportsbook the week is normally published from. */
			__( 'Lines are the nflverse consensus fallback, not %s.', 'trinity-rundown' ),
			trun_get( $game, 'odds.book_label', __( 'the sportsbook', 'trinity-rundown' ) )
		);
	}

	if ( ! empty( $game['odds']['team_totals_derived'] ) ) {
		$flags[] = __( 'Team totals were derived from the spread and total, not posted by the book.', 'trinity-rundown' );
	}

	if ( ! isset( $game['injuries'] ) ) {
		$flags[] = __( 'No injury data has ever been stored for this game.', 'trinity-rundown' );
	}

	if ( trun_admin_has_drift( $row ) ) {
		$flags[] = __( 'The pipeline has moved since this week was published. Re-publish to show the new numbers.', 'trinity-rundown' );
	}

	if ( ! $flags ) {
		return;
	}

	?>
	<ul class="trun-adm__flags">
		<?php foreach ( $flags as $flag ) : ?>
			<li><?php echo esc_html( $flag ); ?></li>
		<?php endforeach; ?>
	</ul>
	<?php
}

/**
 * Has the pipeline moved since this row was frozen?
 *
 * Storage keeps stats_json current while a row is locked, so the live and the
 * published views diverge silently. This is the only place that shows it.
 */
function trun_admin_has_drift( object $row ): bool {
	if ( 1 !== (int) $row->locked || ! $row->published_json ) {
		return false;
	}

	$published = json_decode( (string) $row->published_json, true );
	if ( ! is_array( $published ) ) {
		return false;
	}

	$live = TRUN_Storage::merge_row( $row );

	// _meta is a timestamp and a lock flag, both of which move on their own.
	unset( $live['_meta'], $published['_meta'] );

	return wp_json_encode( $live ) !== wp_json_encode( $published );
}

/**
 * True when any game in the week is frozen.
 *
 * @param object[] $rows
 */
function trun_admin_week_is_locked( array $rows ): bool {
	foreach ( $rows as $row ) {
		if ( 1 === (int) $row->locked ) {
			return true;
		}
	}
	return false;
}

/** Form field name: trun[<game_id>][notes|over][<key>]. */
function trun_admin_field_name( string $game_id, string $group, string $key ): string {
	return sprintf( 'trun[%s][%s][%s]', $game_id, $group, $key );
}

/** A DOM id for the same field, safe to put in a `for` attribute. */
function trun_admin_field_id( string $game_id, string $group, string $key ): string {
	return sanitize_html_class( 'trun-' . $game_id . '-' . $group . '-' . $key );
}

/* -------------------------------------------------------------------------
 * Handlers
 * ---------------------------------------------------------------------- */

add_action( 'admin_post_trun_save_week', 'trun_admin_handle_save' );
add_action( 'admin_post_trun_publish_week', 'trun_admin_handle_publish' );
add_action( 'admin_post_trun_unlock_week', 'trun_admin_handle_unlock' );

/**
 * Guard shared by all three handlers, returning the season and week.
 *
 * @return array{0:int,1:int}
 */
function trun_admin_authorize( string $nonce_action ): array {
	if ( ! current_user_can( TRUN_ADMIN_CAP ) ) {
		wp_die( esc_html__( 'You are not allowed to edit the Rundown.', 'trinity-rundown' ), 403 );
	}

	check_admin_referer( $nonce_action );

	// phpcs:ignore WordPress.Security.NonceVerification.Missing -- verified by check_admin_referer() above.
	$season = isset( $_POST['season'] ) ? (int) $_POST['season'] : 0;
	// phpcs:ignore WordPress.Security.NonceVerification.Missing -- verified by check_admin_referer() above.
	$week = isset( $_POST['week'] ) ? (int) $_POST['week'] : 0;

	if ( $season < 1999 || $week < 1 || $week > 22 ) {
		wp_die( esc_html__( 'That is not a valid season and week.', 'trinity-rundown' ), 400 );
	}

	return [ $season, $week ];
}

function trun_admin_handle_save(): void {
	list( $season, $week ) = trun_admin_authorize( 'trun_save_week' );

	// A game_id is only writable if it is actually in this week, so a forged
	// value cannot reach across into another week's row.
	$known = [];
	foreach ( TRUN_Storage::get_week( $season, $week ) as $row ) {
		$known[] = (string) $row->game_id;
	}

	/*
	 * Submitted values are sanitised per field in the two clean_* helpers
	 * below -- notes through wp_kses_post(), corrections through is_numeric()
	 * or sanitize_text_field() -- which phpcs cannot follow across a function
	 * boundary.
	 */
	// phpcs:ignore WordPress.Security.NonceVerification.Missing, WordPress.Security.ValidatedSanitizedInput.InputNotSanitized
	$submitted = ( isset( $_POST['trun'] ) && is_array( $_POST['trun'] ) ) ? wp_unslash( $_POST['trun'] ) : [];

	$saved   = 0;
	$dropped = 0;

	foreach ( $submitted as $game_id => $fields ) {
		$game_id = (string) $game_id;

		if ( ! is_array( $fields ) || ! in_array( $game_id, $known, true ) ) {
			continue;
		}

		TRUN_Storage::save_editorial(
			$season,
			$week,
			$game_id,
			trun_admin_clean_notes( $fields['notes'] ?? [] ),
			trun_admin_clean_overrides( $fields['over'] ?? [], $dropped )
		);
		++$saved;
	}

	trun_admin_redirect(
		$season,
		$week,
		[
			'trun_notice'  => 'saved',
			'trun_count'   => $saved,
			'trun_dropped' => $dropped,
		]
	);
}

function trun_admin_handle_publish(): void {
	list( $season, $week ) = trun_admin_authorize( 'trun_publish_week' );

	$frozen = TRUN_Storage::publish_week( $season, $week );

	trun_admin_redirect(
		$season,
		$week,
		[
			'trun_notice' => 'published',
			'trun_count'  => $frozen,
		]
	);
}

function trun_admin_handle_unlock(): void {
	list( $season, $week ) = trun_admin_authorize( 'trun_unlock_week' );

	TRUN_Storage::unlock_week( $season, $week );

	trun_admin_redirect( $season, $week, [ 'trun_notice' => 'unlocked' ] );
}

function trun_admin_redirect( int $season, int $week, array $args ): void {
	wp_safe_redirect( trun_admin_url( $season, $week, $args ) );
	exit;
}

/* -------------------------------------------------------------------------
 * Cleaning submitted values
 * ---------------------------------------------------------------------- */

/**
 * Editorial prose, filtered to exactly what the front end will render.
 *
 * render.php passes notes through wp_kses_post( wpautop( ... ) ), so anything
 * stripped here would have been stripped on the way out anyway. Doing it on
 * the way in means what the writer sees on reload is what readers get.
 *
 * @param mixed $raw The `notes` sub-array of one game's submission.
 */
function trun_admin_clean_notes( $raw ): array {
	$notes = [];

	foreach ( array_keys( trun_admin_note_fields() ) as $key ) {
		$value = ( is_array( $raw ) && isset( $raw[ $key ] ) ) ? (string) $raw[ $key ] : '';
		$value = wp_kses_post( trim( $value ) );

		// An empty section stores no key, so notes_json stays {} until someone
		// actually writes something. merge_row() then adds no `notes` at all,
		// which keeps the published-vs-live drift check meaningful: saving a
		// screen of empty boxes is not a change worth flagging.
		if ( '' !== $value ) {
			$notes[ $key ] = $value;
		}
	}

	return $notes;
}

/**
 * Per-field corrections, rebuilt from scratch on every save.
 *
 * Two rules, both load-bearing:
 *
 * - An empty box stores no key at all. storage's deep_merge() lets an override
 *   replace the base value outright, so a stored empty string would blank a
 *   real number on the published page.
 * - The result is not merged into what was stored before. Rebuilding is what
 *   makes clearing a box actually clear it.
 *
 * @param mixed $raw     The `over` sub-array of one game's submission.
 * @param int   $dropped Incremented for each value rejected as non-numeric.
 */
function trun_admin_clean_overrides( $raw, int &$dropped ): array {
	$overrides = [];

	foreach ( trun_admin_override_fields() as $key => $spec ) {
		$value = ( is_array( $raw ) && isset( $raw[ $key ] ) ) ? trim( (string) $raw[ $key ] ) : '';

		if ( '' === $value ) {
			continue;
		}

		if ( 'number' === $spec['type'] ) {
			if ( ! is_numeric( $value ) ) {
				++$dropped;
				continue;
			}
			$clean = (float) $value;
		} else {
			$clean = sanitize_text_field( $value );
			if ( '' === $clean ) {
				continue;
			}
		}

		trun_admin_set_path( $overrides, $spec['path'], $clean );
	}

	return $overrides;
}

/**
 * Write a value at a dot-path, creating the intermediate arrays.
 *
 * The mirror of render.php's trun_get(): the form is flat, the payload is not.
 *
 * @param mixed $value
 */
function trun_admin_set_path( array &$target, string $path, $value ): void {
	$segments = explode( '.', $path );
	$leaf     = array_pop( $segments );

	$node = &$target;
	foreach ( $segments as $segment ) {
		if ( ! isset( $node[ $segment ] ) || ! is_array( $node[ $segment ] ) ) {
			$node[ $segment ] = [];
		}
		$node = &$node[ $segment ];
	}

	$node[ $leaf ] = $value;
	unset( $node );
}

/* -------------------------------------------------------------------------
 * Notices
 * ---------------------------------------------------------------------- */

function trun_admin_render_notice(): void {
	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- display only; the write already happened and redirected here.
	$notice = isset( $_GET['trun_notice'] ) ? sanitize_key( wp_unslash( $_GET['trun_notice'] ) ) : '';
	if ( '' === $notice ) {
		return;
	}

	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- display only.
	$count = isset( $_GET['trun_count'] ) ? (int) $_GET['trun_count'] : 0;
	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- display only.
	$dropped = isset( $_GET['trun_dropped'] ) ? (int) $_GET['trun_dropped'] : 0;

	if ( 'saved' === $notice ) {
		$message = sprintf(
			/* translators: %d: number of games written. */
			_n( 'Saved %d game.', 'Saved %d games.', $count, 'trinity-rundown' ),
			$count
		);
	} elseif ( 'published' === $notice ) {
		$message = sprintf(
			/* translators: %d: number of games frozen. */
			_n( 'Published and froze %d game.', 'Published and froze %d games.', $count, 'trinity-rundown' ),
			$count
		);
	} elseif ( 'unlocked' === $notice ) {
		$message = __( 'Unlocked. The page follows the pipeline again.', 'trinity-rundown' );
	} else {
		return;
	}

	printf(
		'<div class="notice notice-success is-dismissible"><p>%s</p></div>',
		esc_html( $message )
	);

	if ( $dropped > 0 ) {
		printf(
			'<div class="notice notice-warning is-dismissible"><p>%s</p></div>',
			esc_html(
				sprintf(
					/* translators: %d: number of corrections rejected. */
					_n(
						'%d correction was not a number and was ignored.',
						'%d corrections were not numbers and were ignored.',
						$dropped,
						'trinity-rundown'
					),
					$dropped
				)
			)
		);
	}
}
