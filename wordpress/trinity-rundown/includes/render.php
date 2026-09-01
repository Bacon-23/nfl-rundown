<?php
/**
 * Front-end rendering.
 *
 * Everything is emitted server-side so the writeups are in the HTML for
 * search engines and for readers without JavaScript. rundown.js only upgrades
 * the accordion into tabs on wide screens.
 *
 * Phase 0 renders the header/odds bar and the editorial sections. The stat
 * tables land in Phases 1-2 and hang off trun_render_modules().
 */

defined( 'ABSPATH' ) || exit;

/**
 * Render a whole week: glance table plus one accordion panel per game.
 */
function trun_render_week( int $season, int $week ): string {
	$rows = TRUN_Storage::get_week( $season, $week );

	if ( ! $rows ) {
		return current_user_can( 'edit_posts' )
			? '<p class="trun-empty">No Rundown data for ' . esc_html( $season . ' week ' . $week ) . ' yet.</p>'
			: '';
	}

	$games = array_map( [ 'TRUN_Storage', 'view_row' ], $rows );

	ob_start();
	?>
	<div class="trun-week" data-season="<?php echo esc_attr( (string) $season ); ?>" data-week="<?php echo esc_attr( (string) $week ); ?>">
		<?php echo trun_render_glance( $games ); ?>
		<div class="trun-games">
			<?php foreach ( $games as $i => $game ) : ?>
				<?php echo trun_render_game( $game, 0 === $i ); ?>
			<?php endforeach; ?>
		</div>
		<?php echo trun_render_footer( $games ); ?>
	</div>
	<?php
	return (string) ob_get_clean();
}

/**
 * Week-at-a-glance table, so the page says something before anything is opened.
 */
function trun_render_glance( array $games ): string {
	ob_start();
	?>
	<table class="trun-glance">
		<caption class="screen-reader-text"><?php esc_html_e( 'All matchups this week', 'trinity-rundown' ); ?></caption>
		<thead>
			<tr>
				<th scope="col"><?php esc_html_e( 'Matchup', 'trinity-rundown' ); ?></th>
				<th scope="col"><?php esc_html_e( 'Kickoff', 'trinity-rundown' ); ?></th>
				<th scope="col"><?php esc_html_e( 'Spread', 'trinity-rundown' ); ?></th>
				<th scope="col"><?php esc_html_e( 'Total', 'trinity-rundown' ); ?></th>
			</tr>
		</thead>
		<tbody>
		<?php foreach ( $games as $game ) : ?>
			<tr>
				<th scope="row">
					<a href="#<?php echo esc_attr( trun_anchor( $game ) ); ?>"><?php echo esc_html( trun_matchup_label( $game ) ); ?></a>
				</th>
				<td><?php echo esc_html( trun_get( $game, 'kickoff.display', 'TBD' ) ); ?></td>
				<td><?php echo esc_html( trun_spread_text( $game ) ); ?></td>
				<td><?php echo esc_html( trun_get( $game, 'odds.total', '--' ) ); ?></td>
			</tr>
		<?php endforeach; ?>
		</tbody>
	</table>
	<?php
	return (string) ob_get_clean();
}

/**
 * One matchup. A <details> element, so collapsing works with JS disabled.
 */
function trun_render_game( array $game, bool $open = false ): string {
	$away = trun_get( $game, 'away.abbr', '' );
	$home = trun_get( $game, 'home.abbr', '' );

	ob_start();
	?>
	<details class="trun-game" id="<?php echo esc_attr( trun_anchor( $game ) ); ?>"
		<?php echo $open ? ' open' : ''; ?>
		style="<?php echo esc_attr( trun_team_color_vars( $game ) ); ?>">
		<summary class="trun-game__summary">
			<span class="trun-game__teams"><?php echo esc_html( trun_matchup_label( $game ) ); ?></span>
			<span class="trun-game__line"><?php echo esc_html( trun_spread_text( $game ) ); ?></span>
			<span class="trun-game__kick"><?php echo esc_html( trun_get( $game, 'kickoff.display', 'TBD' ) ); ?></span>
		</summary>

		<div class="trun-game__body">
			<?php echo trun_render_odds_bar( $game ); ?>
			<?php echo trun_render_modules( $game ); ?>
			<?php echo trun_render_notes( $game ); ?>
		</div>
	</details>
	<?php
	return (string) ob_get_clean();
}

/**
 * The header strip: records, spread, total, team totals, weather, kickoff.
 */
function trun_render_odds_bar( array $game ): string {
	$cells = [
		[ 'label' => trun_get( $game, 'away.abbr', 'AWAY' ) . ' team total', 'value' => trun_get( $game, 'odds.away_team_total', '--' ) ],
		[ 'label' => trun_get( $game, 'home.abbr', 'HOME' ) . ' team total', 'value' => trun_get( $game, 'odds.home_team_total', '--' ) ],
		[ 'label' => 'Spread', 'value' => trun_spread_text( $game ) ],
		[ 'label' => 'Total', 'value' => (string) trun_get( $game, 'odds.total', '--' ) ],
		[ 'label' => 'Weather', 'value' => trun_get( $game, 'weather.summary', 'TBD' ) ],
		[ 'label' => 'Kickoff', 'value' => trun_get( $game, 'kickoff.display', 'TBD' ) ],
	];

	ob_start();
	?>
	<div class="trun-oddsbar">
		<?php foreach ( $cells as $cell ) : ?>
			<div class="trun-oddsbar__cell">
				<span class="trun-oddsbar__label"><?php echo esc_html( $cell['label'] ); ?></span>
				<span class="trun-oddsbar__value"><?php echo esc_html( (string) $cell['value'] ); ?></span>
			</div>
		<?php endforeach; ?>
	</div>
	<?php
	return (string) ob_get_clean();
}

/**
 * Stat modules, in reading order.
 *
 * Each module renders independently and returns an empty string when it has
 * no data, so a missing feed costs one section rather than the whole page.
 * Efficiency, passing, and rushing slot in here as they land.
 */
function trun_render_modules( array $game ): string {
	return trun_render_injuries( $game );
}

/**
 * Key injuries, both teams in one table.
 *
 * An absent `injuries` key means the source could not be read this run, which
 * is not the same as nobody being hurt. The pipeline omits the key in that
 * case and storage carries the previous value forward, so by the time we get
 * here an empty array genuinely means "nothing to report".
 */
function trun_render_injuries( array $game ): string {
	$rows = isset( $game['injuries'] ) && is_array( $game['injuries'] ) ? $game['injuries'] : [];

	if ( ! $rows ) {
		return '';
	}

	ob_start();
	?>
	<section class="trun-module trun-module--injuries">
		<h3 class="trun-module__heading"><?php esc_html_e( 'Key Injuries', 'trinity-rundown' ); ?></h3>
		<table class="trun-table trun-table--injuries">
			<thead>
				<tr>
					<th scope="col"><?php esc_html_e( 'Team', 'trinity-rundown' ); ?></th>
					<th scope="col"><?php esc_html_e( 'Player', 'trinity-rundown' ); ?></th>
					<th scope="col"><?php esc_html_e( 'Pos', 'trinity-rundown' ); ?></th>
					<th scope="col"><?php esc_html_e( 'Status', 'trinity-rundown' ); ?></th>
					<th scope="col"><?php esc_html_e( 'Note', 'trinity-rundown' ); ?></th>
				</tr>
			</thead>
			<tbody>
			<?php foreach ( $rows as $row ) : ?>
				<?php if ( ! is_array( $row ) || empty( $row['player'] ) ) : continue; endif; ?>
				<tr>
					<td class="trun-table__team"><?php echo esc_html( $row['team'] ?? '' ); ?></td>
					<th scope="row"><?php echo esc_html( $row['player'] ); ?></th>
					<td><?php echo esc_html( $row['position'] ?? '' ); ?></td>
					<td>
						<span class="trun-status trun-status--<?php echo esc_attr( trun_status_slug( $row['status'] ?? '' ) ); ?>">
							<?php echo esc_html( $row['status'] ?? '' ); ?>
						</span>
					</td>
					<td><?php echo esc_html( trun_injury_note( $row ) ); ?></td>
				</tr>
			<?php endforeach; ?>
			</tbody>
		</table>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * Compose the NOTE cell: the injury, then practice participation if known.
 *
 * e.g. "Groin — DNP Wed.", "Knee - ACL", "Full practice".
 */
function trun_injury_note( array $row ): string {
	$parts = [];

	if ( ! empty( $row['note'] ) ) {
		$parts[] = $row['note'];
	}
	if ( ! empty( $row['practice'] ) ) {
		$parts[] = $row['practice'];
	}

	return implode( ' — ', $parts );
}

/**
 * CSS-safe slug for a status, so severity can be styled.
 *
 * Status text is never encoded by color alone; the word itself is always the
 * primary signal.
 */
function trun_status_slug( string $status ): string {
	$slug = sanitize_title( $status );
	return $slug ? $slug : 'unknown';
}

/**
 * Editorial sections. Written in wp-admin, never touched by the pipeline.
 */
function trun_render_notes( array $game ): string {
	$notes = isset( $game['notes'] ) && is_array( $game['notes'] ) ? $game['notes'] : [];

	$sections = [
		'scouting'   => __( 'Scouting Notes', 'trinity-rundown' ),
		'td_leans'   => __( 'Anytime TD Leans', 'trinity-rundown' ),
		'prediction' => __( 'Score Prediction', 'trinity-rundown' ),
	];

	$out = '';
	foreach ( $sections as $key => $heading ) {
		$body = isset( $notes[ $key ] ) ? trim( (string) $notes[ $key ] ) : '';
		if ( '' === $body ) {
			continue;
		}
		$out .= '<section class="trun-notes trun-notes--' . esc_attr( $key ) . '">'
			. '<h3 class="trun-notes__heading">' . esc_html( $heading ) . '</h3>'
			. wp_kses_post( wpautop( $body ) )
			. '</section>';
	}

	return $out;
}

/**
 * Attribution and freshness. Both are conditions of publishing numbers.
 */
function trun_render_footer( array $games ): string {
	$first  = $games[0] ?? [];
	$book   = trun_get( $first, 'odds.book_label', '' );
	$as_of  = trun_get( $first, '_meta.updated_at', '' );
	$source = trun_get( $first, 'odds.source', '' );

	$parts = [];
	if ( $book ) {
		$parts[] = sprintf( __( 'Odds: %s', 'trinity-rundown' ), $book );
	}
	if ( 'nflverse_fallback' === $source ) {
		$parts[] = __( 'consensus fallback in use', 'trinity-rundown' );
	}
	if ( $as_of ) {
		$parts[] = sprintf(
			__( 'Stats as of %s UTC', 'trinity-rundown' ),
			mysql2date( 'M j, Y g:i a', $as_of )
		);
	}

	return $parts
		? '<p class="trun-footer">' . esc_html( implode( ' | ', $parts ) ) . '</p>'
		: '';
}

/* -------------------------------------------------------------------------
 * Small helpers
 * ---------------------------------------------------------------------- */

/**
 * Read a dot-path out of the payload without a stack of isset() checks.
 *
 * Payloads arrive from an external pipeline, so every field is treated as
 * possibly absent -- a missing key renders a dash, never a PHP notice.
 */
function trun_get( array $data, string $path, $default = '' ) {
	$node = $data;
	foreach ( explode( '.', $path ) as $segment ) {
		if ( ! is_array( $node ) || ! array_key_exists( $segment, $node ) ) {
			return $default;
		}
		$node = $node[ $segment ];
	}
	return ( null === $node || '' === $node ) ? $default : $node;
}

function trun_matchup_label( array $game ): string {
	$away = trun_get( $game, 'away.name', trun_get( $game, 'away.abbr', '?' ) );
	$home = trun_get( $game, 'home.name', trun_get( $game, 'home.abbr', '?' ) );
	return $away . ' @ ' . $home;
}

function trun_anchor( array $game ): string {
	return 'trun-' . sanitize_title( (string) trun_get( $game, 'game_id', 'game' ) );
}

/**
 * Render the spread from the favorite's perspective, with movement if it moved.
 */
function trun_spread_text( array $game ): string {
	$spread = trun_get( $game, 'odds.spread', null );
	$fav    = trun_get( $game, 'odds.spread_favorite', '' );

	if ( null === $spread || '' === $spread ) {
		return '--';
	}

	$text = trim( $fav . ' ' . trun_format_spread( (float) $spread ) );

	$open_spread = trun_get( $game, 'odds.opening.spread', null );
	$open_fav    = trun_get( $game, 'odds.opening.spread_favorite', $fav );

	if ( null !== $open_spread && '' !== $open_spread ) {
		$moved = ( (float) $open_spread !== (float) $spread ) || ( $open_fav !== $fav );
		if ( $moved ) {
			$text .= sprintf(
				/* translators: %s: the opening line, e.g. "SEA -3.5" */
				__( ' (opened %s)', 'trinity-rundown' ),
				trim( $open_fav . ' ' . trun_format_spread( (float) $open_spread ) )
			);
		}
	}

	return $text;
}

/** Spreads read as -4.5 and +3, never -4.50 or +3.0. */
function trun_format_spread( float $spread ): string {
	$abs    = abs( $spread );
	$number = ( floor( $abs ) === $abs ) ? number_format( $abs, 0 ) : number_format( $abs, 1 );
	if ( 0.0 === $spread ) {
		return 'PK';
	}
	return ( $spread < 0 ? '-' : '+' ) . $number;
}

/**
 * Expose team colors to CSS as custom properties, scoped to this game.
 */
function trun_team_color_vars( array $game ): string {
	$away = trun_get( $game, 'away.color', '' );
	$home = trun_get( $game, 'home.color', '' );

	$vars = '';
	if ( preg_match( '/^#[0-9a-f]{6}$/i', (string) $away ) ) {
		$vars .= '--trun-away:' . $away . ';';
	}
	if ( preg_match( '/^#[0-9a-f]{6}$/i', (string) $home ) ) {
		$vars .= '--trun-home:' . $home . ';';
	}
	return $vars;
}
