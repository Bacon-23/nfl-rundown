<?php
/**
 * Front-end rendering.
 *
 * Everything is emitted server-side so the writeups are in the HTML for
 * search engines and for readers without JavaScript. rundown.js only upgrades
 * the accordion into tabs on wide screens.
 *
 * The header and odds bar come first, then the stat tables off
 * trun_render_modules(), then the editorial sections. Every stat value is a
 * fraction in the payload and becomes a percentage here; see docs/metrics.md.
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
		[
			'label' => trun_get( $game, 'away.abbr', 'AWAY' ) . ' team total',
			'value' => trun_get( $game, 'odds.away_team_total', '--' ),
		],
		[
			'label' => trun_get( $game, 'home.abbr', 'HOME' ) . ' team total',
			'value' => trun_get( $game, 'odds.home_team_total', '--' ),
		],
		[
			'label' => 'Spread',
			'value' => trun_spread_text( $game ),
		],
		[
			'label' => 'Total',
			'value' => (string) trun_get( $game, 'odds.total', '--' ),
		],
		[
			'label' => 'Weather',
			'value' => trun_get( $game, 'weather.summary', 'TBD' ),
		],
		[
			'label' => 'Kickoff',
			'value' => trun_get( $game, 'kickoff.display', 'TBD' ),
		],
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
 */
function trun_render_modules( array $game ): string {
	return trun_render_injuries( $game )
		. trun_render_efficiency( $game )
		. trun_render_passing( $game )
		. trun_render_rushing( $game );
}

/**
 * Team efficiency: how these two offenses play, before anyone is named.
 *
 * Every rate in the payload is a fraction, so one formatter handles the lot.
 * See docs/metrics.md for what each column actually measures -- "pace" in
 * particular has no single industry definition, and ours is spelled out.
 */
function trun_render_efficiency( array $game ): string {
	$rows = trun_module_rows( $game, 'efficiency.rows' );

	if ( ! $rows ) {
		return '';
	}

	$proe_heading = trun_abbr(
		__( 'PROE', 'trinity-rundown' ),
		__( "Pass rate over expected, against nflfastR's model. Full season.", 'trinity-rundown' )
	);

	ob_start();
	?>
	<section class="trun-module trun-module--efficiency">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Team Efficiency', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'efficiency' ); ?>
		</h3>
		<div class="trun-scroll">
			<table class="trun-table trun-table--efficiency">
				<thead>
					<tr>
						<th scope="col"><?php esc_html_e( 'Team', 'trinity-rundown' ); ?></th>
						<th scope="col"><?php esc_html_e( 'Pass rate', 'trinity-rundown' ); ?></th>
						<th scope="col"><?php esc_html_e( 'Rush rate', 'trinity-rundown' ); ?></th>
						<th scope="col"><?php echo $proe_heading; ?></th>
						<th scope="col"><?php esc_html_e( 'Pace (sec/play)', 'trinity-rundown' ); ?></th>
						<th scope="col"><?php esc_html_e( 'Plays/gm', 'trinity-rundown' ); ?></th>
						<th scope="col"><?php esc_html_e( 'EPA/play (rk)', 'trinity-rundown' ); ?></th>
					</tr>
				</thead>
				<tbody>
				<?php foreach ( $rows as $row ) : ?>
					<tr>
						<th scope="row"><?php echo esc_html( $row['team'] ?? '' ); ?></th>
						<td><?php echo esc_html( trun_percent( $row['pass_rate'] ?? null ) ); ?></td>
						<td><?php echo esc_html( trun_percent( $row['rush_rate'] ?? null ) ); ?></td>
						<td><?php echo esc_html( trun_percent( $row['proe'] ?? null, 1, true ) ); ?></td>
						<td><?php echo esc_html( trun_decimal( $row['pace'] ?? null, 1 ) ); ?></td>
						<td><?php echo esc_html( trun_decimal( $row['plays_per_game'] ?? null, 1 ) ); ?></td>
						<td><?php echo esc_html( trun_epa_cell( $row ) ); ?></td>
					</tr>
				<?php endforeach; ?>
				</tbody>
			</table>
		</div>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * Passing game, one table per side.
 *
 * The third column is the one to be careful about. It is NOT targets per route
 * run -- that needs charted route data we do not license -- so it is labeled
 * TGT RATE and carries a tooltip saying what it actually is. Do not rename it
 * to TPRR, however much the mockup wants to.
 */
function trun_render_passing( array $game ): string {
	$sides = trun_module_sides( $game, 'passing' );

	if ( ! $sides ) {
		return '';
	}

	// The one heading on the page that would mislead without its tooltip.
	$rate_heading = trun_abbr(
		__( 'Tgt rate', 'trinity-rundown' ),
		__( 'Targets per estimated pass snap -- a proxy for TPRR, which requires charted route data.', 'trinity-rundown' )
	);

	ob_start();
	?>
	<section class="trun-module trun-module--passing">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Passing Game', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'passing' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<div class="trun-scroll">
				<table class="trun-table trun-table--passing">
					<caption class="trun-table__caption"><?php echo esc_html( $side['label'] ); ?></caption>
					<thead>
						<tr>
							<th scope="col"><?php esc_html_e( 'Player', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Role', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Tgt share', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php echo $rate_heading; ?></th>
							<th scope="col"><?php esc_html_e( 'Rec yds/gm', 'trinity-rundown' ); ?></th>
						</tr>
					</thead>
					<tbody>
					<?php foreach ( $side['rows'] as $row ) : ?>
						<tr>
							<th scope="row"><?php echo esc_html( $row['player'] ?? '' ); ?></th>
							<td><?php echo esc_html( $row['role'] ?? '' ); ?></td>
							<td><?php echo esc_html( trun_percent( $row['target_share'] ?? null, 1 ) ); ?></td>
							<td><?php echo esc_html( trun_percent( $row['target_rate'] ?? null, 1 ) ); ?></td>
							<td><?php echo esc_html( trun_decimal( $row['rec_yds_per_game'] ?? null, 1 ) ); ?></td>
						</tr>
					<?php endforeach; ?>
					</tbody>
				</table>
			</div>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * Running back workload, one table per side.
 *
 * Sorted on snap share rather than carries: 14 carries in a blowout and 14 in
 * a one-score game are not the same workload, and the snap column says so.
 */
function trun_render_rushing( array $game ): string {
	$sides = trun_module_sides( $game, 'rushing' );

	if ( ! $sides ) {
		return '';
	}

	ob_start();
	?>
	<section class="trun-module trun-module--rushing">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Running Back Workload', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'rushing' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<div class="trun-scroll">
				<table class="trun-table trun-table--rushing">
					<caption class="trun-table__caption"><?php echo esc_html( $side['label'] ); ?></caption>
					<thead>
						<tr>
							<th scope="col"><?php esc_html_e( 'Player', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Snap %', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Rush att/gm', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Tgt share', 'trinity-rundown' ); ?></th>
							<th scope="col"><?php esc_html_e( 'Yds/att', 'trinity-rundown' ); ?></th>
						</tr>
					</thead>
					<tbody>
					<?php foreach ( $side['rows'] as $row ) : ?>
						<tr>
							<th scope="row"><?php echo esc_html( $row['player'] ?? '' ); ?></th>
							<td><?php echo esc_html( trun_percent( $row['snap_share'] ?? null ) ); ?></td>
							<td><?php echo esc_html( trun_decimal( $row['rush_att_per_game'] ?? null, 1 ) ); ?></td>
							<td><?php echo esc_html( trun_percent( $row['target_share'] ?? null, 1 ) ); ?></td>
							<td><?php echo esc_html( trun_decimal( $row['yards_per_att'] ?? null, 1 ) ); ?></td>
						</tr>
					<?php endforeach; ?>
					</tbody>
				</table>
			</div>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
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
				<?php
				if ( ! is_array( $row ) || empty( $row['player'] ) ) :
					continue;
endif;
				?>
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
		/* translators: %s: sportsbook the lines were taken from, e.g. DraftKings. */
		$parts[] = sprintf( __( 'Odds: %s', 'trinity-rundown' ), $book );
	}
	if ( 'nflverse_fallback' === $source ) {
		$parts[] = __( 'consensus fallback in use', 'trinity-rundown' );
	}
	if ( $as_of ) {
		$parts[] = sprintf(
			/* translators: %s: date and time of the last stats refresh. */
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
function trun_get( array $data, string $path, $fallback = '' ) {
	$node = $data;
	foreach ( explode( '.', $path ) as $segment ) {
		if ( ! is_array( $node ) || ! array_key_exists( $segment, $node ) ) {
			return $fallback;
		}
		$node = $node[ $segment ];
	}
	return ( null === $node || '' === $node ) ? $fallback : $node;
}

/**
 * A module's row list, or an empty array when the module is absent.
 *
 * Pipeline payloads are treated as possibly-absent at every level: a module
 * that failed upstream is simply not in the JSON, and that has to render as a
 * missing section rather than as a PHP notice.
 */
function trun_module_rows( array $game, string $path ): array {
	$rows = trun_get( $game, $path, [] );
	return is_array( $rows ) ? array_filter( $rows, 'is_array' ) : [];
}

/**
 * The away and home halves of a two-sided module, labeled with the team.
 *
 * A side with no rows is dropped rather than rendered as an empty table: one
 * team having no listed backs is a real possibility, and a header over nothing
 * reads as a bug.
 */
function trun_module_sides( array $game, string $module ): array {
	$sides = [];

	foreach ( [ 'away', 'home' ] as $side ) {
		$rows = trun_module_rows( $game, $module . '.' . $side );
		if ( ! $rows ) {
			continue;
		}
		$sides[] = [
			'label' => (string) trun_get( $game, $side . '.name', trun_get( $game, $side . '.abbr', '' ) ),
			'rows'  => $rows,
		];
	}

	return $sides;
}

/**
 * The sample badge: "2025 season", "n = 3 games", or nothing at all.
 *
 * Week 1 publishes last season's numbers and weeks 2 to 4 publish a thin
 * sample. Both say so on the page -- that is the whole point of the badge, and
 * it is the pipeline that decides the wording, not this function.
 */
function trun_render_badge( array $game, string $module ): string {
	$badge = trun_get( $game, $module . '.badge', '' );

	if ( '' === $badge ) {
		return '';
	}

	return '<span class="trun-badge">' . esc_html( (string) $badge ) . '</span>';
}

/**
 * A column heading with an explanation attached.
 *
 * Used where the label alone would mislead. TGT RATE is the case that matters:
 * it is not TPRR, and the tooltip is what keeps the distinction on the page
 * rather than only in the docs.
 */
function trun_abbr( string $label, string $explanation ): string {
	return '<abbr title="' . esc_attr( $explanation ) . '">' . esc_html( $label ) . '</abbr>';
}

/**
 * Render a fraction as a percentage.
 *
 * Everything in the payload is stored as a fraction between 0 and 1, so this
 * is the only place a percent sign is applied. `$signed` is for PROE, where
 * the direction is the whole point and "+2.4%" says more than "2.4%".
 */
function trun_percent( $value, int $places = 0, bool $signed = false ): string {
	if ( null === $value || '' === $value || ! is_numeric( $value ) ) {
		return '--';
	}

	$percent = (float) $value * 100;
	$text    = number_format( $percent, $places ) . '%';

	if ( $signed && $percent > 0 ) {
		$text = '+' . $text;
	}

	return $text;
}

/** A plain number, or a dash. Never "0" standing in for "we do not know". */
function trun_decimal( $value, int $places = 1 ): string {
	if ( null === $value || '' === $value || ! is_numeric( $value ) ) {
		return '--';
	}

	return number_format( (float) $value, $places );
}

/**
 * The EPA cell: the number and where it ranks, e.g. "+0.16 (1st)".
 *
 * The rank alone is what the mockup shows, but a rank with no value behind it
 * cannot be checked against anything.
 */
function trun_epa_cell( array $row ): string {
	$epa = $row['epa_per_play'] ?? null;

	if ( null === $epa || ! is_numeric( $epa ) ) {
		return '--';
	}

	$text = ( $epa > 0 ? '+' : '' ) . number_format( (float) $epa, 2 );
	$rank = $row['epa_rank'] ?? null;

	if ( $rank && is_numeric( $rank ) ) {
		$text .= ' (' . trun_ordinal( (int) $rank ) . ')';
	}

	return $text;
}

/** 1 -> "1st", 2 -> "2nd", 11 -> "11th". */
function trun_ordinal( int $number ): string {
	$suffix = 'th';

	if ( ! in_array( $number % 100, [ 11, 12, 13 ], true ) ) {
		$suffix = [ 'th', 'st', 'nd', 'rd' ][ $number % 10 ] ?? 'th';
	}

	return $number . $suffix;
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
