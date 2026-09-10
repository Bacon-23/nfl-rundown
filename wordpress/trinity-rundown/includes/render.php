<?php
/**
 * Front-end rendering.
 *
 * Everything is emitted server-side so the writeups are in the HTML for
 * search engines and for readers without JavaScript: the page is a list of
 * <details> panels and the browser does the collapsing. The only script is
 * rundown.js, and it only drives the expand-all control, which renders hidden
 * until the script unhides it -- so without JavaScript the page is unchanged.
 *
 * The header and odds bar come first, then the stat tables off
 * trun_render_modules(), then the editorial sections. Every stat value is a
 * fraction in the payload and becomes a percentage here; see docs/metrics.md.
 */

defined( 'ABSPATH' ) || exit;

/**
 * Render a whole week: one collapsible panel per game.
 */
function trun_render_week( int $season, int $week ): string {
	return trun_render_rows( TRUN_Storage::get_week( $season, $week ), $season, $week );
}

/**
 * Render rows that have already been fetched.
 *
 * Split from trun_render_week() so the page can be produced from rows built by
 * hand -- which is what tools/preview.php does to render a payload locally,
 * without a database. Everything downstream of here is pure: rows in, HTML out.
 */
function trun_render_rows( array $rows, int $season, int $week ): string {
	if ( ! $rows ) {
		return current_user_can( 'edit_posts' )
			? '<p class="trun-empty">No Rundown data for ' . esc_html( $season . ' week ' . $week ) . ' yet.</p>'
			: '';
	}

	$games = array_map( [ 'TRUN_Storage', 'view_row' ], $rows );

	ob_start();
	?>
	<div class="trun-week" data-season="<?php echo esc_attr( (string) $season ); ?>" data-week="<?php echo esc_attr( (string) $week ); ?>">
		<?php echo trun_render_toolbar( $games ); ?>
		<div class="trun-games">
			<?php foreach ( $games as $game ) : ?>
				<?php echo trun_render_game( $game ); ?>
			<?php endforeach; ?>
		</div>
		<?php echo trun_render_footer( $games ); ?>
	</div>
	<?php
	return (string) ob_get_clean();
}

/**
 * The one control above the week: open every panel, or close every panel.
 *
 * Opening sixteen games one at a time is the whole week's reading, and closing
 * them again to find one is worse. This is the only thing on the page that
 * needs script, so it is the only thing that degrades: it renders `hidden` and
 * rundown.js unhides it. A reader without JavaScript sees the panels exactly
 * as before rather than a button that does nothing.
 *
 * Both labels ship as data attributes rather than being written in the script,
 * so the string stays translatable in PHP and the script stays string-free.
 */
function trun_render_toolbar( array $games ): string {
	// One game is not a set to expand. Nothing to control, so no control.
	if ( count( $games ) < 2 ) {
		return '';
	}

	ob_start();
	?>
	<div class="trun-toolbar" hidden>
		<button type="button" class="trun-toolbar__all" data-trun-toggle-all
			data-label-expand="<?php echo esc_attr( __( 'Expand all', 'trinity-rundown' ) ); ?>"
			data-label-collapse="<?php echo esc_attr( __( 'Collapse all', 'trinity-rundown' ) ); ?>">
			<?php echo esc_html( __( 'Expand all', 'trinity-rundown' ) ); ?>
		</button>
	</div>
	<?php
	return (string) ob_get_clean();
}

/**
 * One matchup. A <details> element, so the browser does the collapsing and
 * every panel renders closed -- a week is sixteen headers until one is opened.
 */
function trun_render_game( array $game ): string {
	$away = trun_get( $game, 'away.abbr', '' );
	$home = trun_get( $game, 'home.abbr', '' );

	ob_start();
	?>
	<details class="trun-game" id="<?php echo esc_attr( trun_anchor( $game ) ); ?>"
		style="<?php echo esc_attr( trun_team_color_vars( $game ) ); ?>">
		<summary class="trun-game__summary">
			<span class="trun-game__teams"><?php echo esc_html( trun_matchup_label( $game ) ); ?></span>
			<span class="trun-game__line"><?php echo esc_html( trun_spread_text( $game ) ); ?></span>
			<span class="trun-game__kick"><?php echo esc_html( trun_get( $game, 'kickoff.display', 'TBD' ) ); ?></span>
		</summary>

		<div class="trun-game__body">
			<?php echo trun_render_teambar( $game ); ?>
			<?php echo trun_render_odds_bar( $game ); ?>
			<?php echo trun_render_records( $game ); ?>
			<?php echo trun_render_modules( $game ); ?>
			<?php echo trun_render_notes( $game ); ?>
		</div>
	</details>
	<?php
	return (string) ob_get_clean();
}

/**
 * Who is playing: logo, name, straight-up record, moneyline, per side.
 *
 * The payload has carried records, moneylines and logos since Phase 1 and the
 * page showed none of them -- a reader arrived at a stat table without being
 * told whose. This is the mockup's top block.
 */
function trun_render_teambar( array $game ): string {
	$sides = [
		'away' => __( 'Away', 'trinity-rundown' ),
		'home' => __( 'Home', 'trinity-rundown' ),
	];

	ob_start();
	?>
	<div class="trun-teambar">
		<?php foreach ( $sides as $side => $role ) : ?>
			<?php
			$logo = esc_url( (string) trun_get( $game, $side . '.logo', '' ) );
			$meta = array_filter(
				[
					(string) trun_get( $game, $side . '.record', '' ),
					trun_format_moneyline( trun_get( $game, $side . '.moneyline', null ) ),
				]
			);
			?>
			<div class="trun-teambar__side trun-teambar__side--<?php echo esc_attr( $side ); ?>">
				<?php if ( $logo ) : ?>
					<?php /* Decorative: the team's name is the next element along. */ ?>
					<img class="trun-teambar__logo" src="<?php echo $logo; ?>"
						alt="" width="48" height="48" loading="lazy" decoding="async">
				<?php endif; ?>
				<span class="trun-teambar__role"><?php echo esc_html( $role ); ?></span>
				<span class="trun-teambar__name">
					<?php echo esc_html( (string) trun_get( $game, $side . '.name', trun_get( $game, $side . '.abbr', '' ) ) ); ?>
				</span>
				<?php if ( $meta ) : ?>
					<span class="trun-teambar__meta"><?php echo esc_html( implode( ' · ', $meta ) ); ?></span>
				<?php endif; ?>
			</div>
		<?php endforeach; ?>
	</div>
	<?php
	return (string) ob_get_clean();
}

/**
 * The market strip: team totals, spread, total, weather, kickoff.
 */
function trun_render_odds_bar( array $game ): string {
	// Books post 24 and 24.5 alike to one decimal, and a column mixing "24"
	// with "20.5" reads as two different kinds of number.
	$cells = [
		[
			'label' => trun_get( $game, 'away.abbr', 'AWAY' ) . ' team total',
			'value' => trun_decimal( trun_get( $game, 'odds.away_team_total', null ), 1 ),
		],
		[
			'label' => trun_get( $game, 'home.abbr', 'HOME' ) . ' team total',
			'value' => trun_decimal( trun_get( $game, 'odds.home_team_total', null ), 1 ),
		],
		[
			'label' => __( 'Spread', 'trinity-rundown' ),
			'value' => trun_spread_bare( $game ),
			'note'  => trun_opening_text( $game ),
		],
		[
			'label' => __( 'Total', 'trinity-rundown' ),
			'value' => trun_decimal( trun_get( $game, 'odds.total', null ), 1 ),
		],
		[
			'label' => __( 'Weather', 'trinity-rundown' ),
			'value' => trun_get( $game, 'weather.summary', 'TBD' ),
		],
		[
			'label' => __( 'Kickoff', 'trinity-rundown' ),
			'value' => trun_get( $game, 'kickoff.display', 'TBD' ),
		],
	];

	return trun_render_cells( $cells );
}

/**
 * Season records against the spread and the total, both sides.
 *
 * Week 1 shows last season's, which is what the badge on every stat module
 * says too -- the cutover is `config.stats_season()` in the pipeline, so there
 * is nothing to decide here.
 */
function trun_render_records( array $game ): string {
	$away = trun_get( $game, 'away.abbr', 'AWAY' );
	$home = trun_get( $game, 'home.abbr', 'HOME' );

	$ats = __( 'Result against the closing spread. A game landing exactly on the number is a push, counted separately.', 'trinity-rundown' );
	$ou  = __( 'Combined points against the closing total. Wins count overs, losses count unders.', 'trinity-rundown' );

	$cells = [
		[
			'label_html' => trun_abbr( $away . ' ATS', $ats ) . ' <span class="trun-oddsbar__qualifier">(season)</span>',
			'value'      => trun_get( $game, 'away.ats_record', '--' ),
		],
		[
			'label_html' => trun_abbr( $home . ' ATS', $ats ) . ' <span class="trun-oddsbar__qualifier">(season)</span>',
			'value'      => trun_get( $game, 'home.ats_record', '--' ),
		],
		[
			'label_html' => trun_abbr( $away . ' O/U', $ou ) . ' <span class="trun-oddsbar__qualifier">(season)</span>',
			'value'      => trun_get( $game, 'away.ou_record', '--' ),
		],
		[
			'label_html' => trun_abbr( $home . ' O/U', $ou ) . ' <span class="trun-oddsbar__qualifier">(season)</span>',
			'value'      => trun_get( $game, 'home.ou_record', '--' ),
		],
	];

	return trun_render_cells( $cells, 'trun-oddsbar--records' );
}

/**
 * The label-over-value grid both strips above are made of.
 *
 * A cell carries either a plain `label`, escaped here, or a `label_html` that
 * has already been built out of trun_abbr() and escaped on the way in.
 */
function trun_render_cells( array $cells, string $modifier = '' ): string {
	ob_start();
	?>
	<div class="trun-oddsbar <?php echo esc_attr( $modifier ); ?>">
		<?php foreach ( $cells as $cell ) : ?>
			<div class="trun-oddsbar__cell">
				<span class="trun-oddsbar__label">
					<?php
					echo isset( $cell['label_html'] )
						? $cell['label_html']
						: esc_html( (string) $cell['label'] );
					?>
				</span>
				<span class="trun-oddsbar__value"><?php echo esc_html( (string) $cell['value'] ); ?></span>
				<?php if ( ! empty( $cell['note'] ) ) : ?>
					<span class="trun-oddsbar__note"><?php echo esc_html( (string) $cell['note'] ); ?></span>
				<?php endif; ?>
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
		. trun_render_rushing( $game )
		. trun_render_fantasy( $game )
		. trun_render_kicking( $game );
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

	$play_types = __( 'Plays where the play type is pass or run. Kneels and spikes are clock management, not play-calling, and are excluded.', 'trinity-rundown' );

	$columns = [
		[
			'label' => __( 'Team', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['team'] ?? '' ),
		],
		[
			'label' => __( 'Pass rate', 'trinity-rundown' ),
			'tip'   => $play_types,
			'cell'  => static fn( $row ) => trun_percent( $row['pass_rate'] ?? null ),
		],
		[
			'label' => __( 'Rush rate', 'trinity-rundown' ),
			'tip'   => $play_types,
			'cell'  => static fn( $row ) => trun_percent( $row['rush_rate'] ?? null ),
		],
		[
			'label' => __( 'PROE', 'trinity-rundown' ),
			'tip'   => __( "Pass rate over expected, against nflfastR's model. Full season.", 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['proe'] ?? null, 1, true ),
		],
		[
			'label' => __( 'Pace (sec/play)', 'trinity-rundown' ),
			'tip'   => __( 'Mean seconds between snaps on the same possession, at neutral win probability on first and second down. "Pace" has no single industry definition; this is ours, so it will not match another site exactly.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['pace'] ?? null, 1 ),
		],
		[
			'label' => __( 'Plays/gm', 'trinity-rundown' ),
			'tip'   => __( 'Offensive plays, kneels and spikes excluded, divided by games played.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['plays_per_game'] ?? null, 1 ),
		],
		[
			'label' => __( 'EPA/play (rk)', 'trinity-rundown' ),
			'tip'   => __( 'Mean offensive expected points added on pass and run plays, ranked 1 to 32 across the league.', 'trinity-rundown' ),
			'cell'  => 'trun_epa_cell',
		],
	];

	ob_start();
	?>
	<section class="trun-module trun-module--efficiency">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Team Efficiency', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'efficiency' ); ?>
		</h3>
		<?php echo trun_render_stat_table( $columns, $rows, 'trun-table--efficiency' ); ?>
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

	$columns = [
		[
			'label' => __( 'Player', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Role', 'trinity-rundown' ),
			'tip'   => __( 'Numbered within position across the whole team, and assigned before the five-row cut -- so a team\'s WR3 is its third receiver, not the third name left in the table.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['role'] ?? '' ),
		],
		[
			'label' => __( 'Tgt share', 'trinity-rundown' ),
			'tip'   => __( 'Player targets divided by team targets, season to date.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['target_share'] ?? null, 1 ),
		],
		[
			// The one heading on the page that would mislead without its tooltip.
			'label' => __( 'Tgt rate', 'trinity-rundown' ),
			'tip'   => __( 'Targets per estimated pass snap -- a proxy for TPRR, which requires charted route data. It reads high against a true TPRR figure; the ranking is sound, the level is not comparable.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['target_rate'] ?? null, 1 ),
		],
		[
			'label' => __( 'Rec yds/gm', 'trinity-rundown' ),
			'tip'   => __( 'Receiving yards divided by games with at least one offensive snap, so weeks missed entirely do not drag the average down.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['rec_yds_per_game'] ?? null, 1 ),
		],
	];

	ob_start();
	?>
	<section class="trun-module trun-module--passing">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Passing Game', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'passing' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<?php echo trun_render_stat_table( $columns, $side['rows'], 'trun-table--passing', $side['label'], $side['side'] ); ?>
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

	$columns = [
		[
			'label' => __( 'Player', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Snap %', 'trinity-rundown' ),
			'tip'   => __( 'Pro Football Reference offensive snap share, averaged over games the player appeared in rather than over the season. The table is sorted on this.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['snap_share'] ?? null ),
		],
		[
			'label' => __( 'Rush att/gm', 'trinity-rundown' ),
			'tip'   => __( 'Rushing attempts divided by games with a snap. A back needs one attempt a game to appear here at all.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['rush_att_per_game'] ?? null, 1 ),
		],
		[
			'label' => __( 'Tgt share', 'trinity-rundown' ),
			'tip'   => __( 'Player targets divided by team targets, season to date. The same figure, from the same code, as in the passing table.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['target_share'] ?? null, 1 ),
		],
		[
			'label' => __( 'Yds/att', 'trinity-rundown' ),
			'tip'   => __( 'Rushing yards divided by rushing attempts.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['yards_per_att'] ?? null, 1 ),
		],
	];

	ob_start();
	?>
	<section class="trun-module trun-module--rushing">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Running Back Workload', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'rushing' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<?php echo trun_render_stat_table( $columns, $side['rows'], 'trun-table--rushing', $side['label'], $side['side'] ); ?>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * PPR at home and on the road, one table per side.
 *
 * The badge on this module says "last 17 games", which is a claim about the
 * window rather than about any one player -- a receiver in his second season
 * has nine. So each venue cell carries its own game count: "20.0 (8)" is a
 * different statement from "20.0 (17)", and the table would be dishonest
 * without the parenthetical.
 *
 * A cell reading "-- (2)" is the pipeline saying it had two games and would
 * not average them. That is deliberately distinct from a bare dash, which
 * means no data at all.
 */
function trun_render_fantasy( array $game ): string {
	$sides = trun_module_sides( $game, 'fantasy' );

	if ( ! $sides ) {
		return '';
	}

	$window = __( 'The player\'s last 17 games, which reaches back into last season rather than waiting for this one to build a sample. Splitting by venue halves whatever sample it is given.', 'trinity-rundown' );

	$columns = [
		[
			'label' => __( 'Player', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Pos', 'trinity-rundown' ),
			'tip'   => __( 'The quarterback is pinned to the top of each table rather than ranked into it. On raw PPR he outscores his own receivers on almost every team, so ranking him would cost a skill-player row and tell you nothing.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['position'] ?? '' ),
		],
		[
			'label' => __( 'PPR/gm', 'trinity-rundown' ),
			'tip'   => __( 'Full PPR as nflverse scores it: one point per reception, one per 25 passing yards, four for a passing touchdown, a tenth per rushing and receiving yard. Averaged over games played, not weeks elapsed.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['ppr_per_game'] ?? null, 1 ),
		],
		[
			'label' => __( 'Home', 'trinity-rundown' ),
			'tip'   => $window,
			'cell'  => static fn( $row ) => trun_venue_cell( $row, 'home' ),
		],
		[
			'label' => __( 'Away', 'trinity-rundown' ),
			'tip'   => $window,
			'cell'  => static fn( $row ) => trun_venue_cell( $row, 'away' ),
		],
		[
			'label' => __( 'Split', 'trinity-rundown' ),
			'tip'   => __( 'Home average minus away average. Blank when either side rests on fewer than three games -- a split measured against a dash is not a split.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_signed( $row['ppr_split'] ?? null, 1 ),
		],
	];

	ob_start();
	?>
	<section class="trun-module trun-module--fantasy">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Fantasy - PPR', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'fantasy' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<?php echo trun_render_stat_table( $columns, $side['rows'], 'trun-table--fantasy', $side['label'], $side['side'] ); ?>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * Kicker accuracy by venue, one table per side.
 *
 * No points column, deliberately. nflverse scores every kicker zero -- its
 * fantasy formula excludes kicking outright -- so any points here would be a
 * scoring rule we invented, and the reader's league would disagree with it.
 * Made, attempted, and long are facts.
 *
 * The weather line repeats under the heading because it is the reason this
 * table exists: a dome, an altitude, and a crosswind are exactly what a home
 * and away split is measuring, and they are twenty rows up the page.
 */
function trun_render_kicking( array $game ): string {
	$sides = trun_module_sides( $game, 'kicking' );

	if ( ! $sides ) {
		return '';
	}

	$columns = [
		[
			'label' => __( 'Split', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => 'home' === ( $row['venue'] ?? '' )
				? __( 'At home', 'trinity-rundown' )
				: __( 'On the road', 'trinity-rundown' ),
		],
		[
			'label' => __( 'FG', 'trinity-rundown' ),
			'tip'   => __( 'Field goals made and attempted at that venue over the kicker\'s last 17 games. Blocks count as attempts, the way every kicking table counts them.', 'trinity-rundown' ),
			'cell'  => 'trun_fg_cell',
		],
		[
			'label' => __( 'FG%', 'trinity-rundown' ),
			'tip'   => __( 'Made divided by attempted at that venue. A kicker needs five attempts across the whole window before he appears at all: two-for-two is not a hundred percent of anything.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['fg_pct'] ?? null, 1 ),
		],
		[
			'label' => __( 'Long', 'trinity-rundown' ),
			'tip'   => __( 'Longest field goal made at that venue during the window.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['fg_long'] ?? null, 0 ),
		],
		[
			'label' => __( 'Att/gm', 'trinity-rundown' ),
			'tip'   => __( 'Attempts divided by games played at that venue. Volume is the half of a kicker that his offense controls.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['fg_att_per_game'] ?? null, 1 ),
		],
	];

	$weather = (string) trun_get( $game, 'weather.summary', '' );

	ob_start();
	?>
	<section class="trun-module trun-module--kicking">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Kickers', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'kicking' ); ?>
		</h3>
		<?php if ( '' !== $weather ) : ?>
			<p class="trun-module__note"><?php echo esc_html( $weather ); ?></p>
		<?php endif; ?>
		<?php foreach ( $sides as $side ) : ?>
			<?php echo trun_render_stat_table( $columns, $side['rows'], 'trun-table--kicking', trun_kicker_caption( $side ), $side['side'] ); ?>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * "Seattle Seahawks - Jason Myers", or just the team when nobody is named.
 *
 * The kicker's name repeats on both of his rows in the payload precisely so
 * the caption can be built here, without a module-level field that
 * trun_module_sides() has no way to reach.
 */
function trun_kicker_caption( array $side ): string {
	$team   = (string) ( $side['label'] ?? '' );
	$kicker = (string) ( $side['rows'][0]['player'] ?? '' );

	if ( '' === $kicker ) {
		return $team;
	}

	/* translators: 1: team name, 2: kicker's name */
	return sprintf( __( '%1$s - %2$s', 'trinity-rundown' ), $team, $kicker );
}

/**
 * A venue average with the games behind it, e.g. "20.0 (8)".
 *
 * The count is not decoration. This module's badge quotes a 17-game window,
 * but a player who has played nine gets nine, and without the parenthetical
 * the two read identically.
 *
 * "-- (2)" is a real state: the pipeline had two games at that venue and
 * refused to average them. A bare dash means it had nothing at all, and
 * collapsing the two would hide the difference between a thin sample and a
 * broken join.
 */
function trun_venue_cell( array $row, string $venue ): string {
	$value = $row[ 'ppr_' . $venue ] ?? null;
	$games = $row[ $venue . '_games' ] ?? null;
	$count = is_numeric( $games ) ? ' (' . (int) $games . ')' : '';

	if ( null === $value || ! is_numeric( $value ) ) {
		return '--' . $count;
	}

	return number_format( (float) $value, 1 ) . $count;
}

/**
 * A signed number, for a differential where the direction is the point.
 *
 * trun_percent()'s $signed flag does the same job for rates; this one is for
 * a plain figure, where multiplying by a hundred would be wrong.
 */
function trun_signed( $value, int $places = 1 ): string {
	if ( null === $value || '' === $value || ! is_numeric( $value ) ) {
		return '--';
	}

	$number = (float) $value;

	return ( $number > 0 ? '+' : '' ) . number_format( $number, $places );
}

/** Made over attempted, e.g. "13/14". A dash when he never lined one up. */
function trun_fg_cell( array $row ): string {
	$attempts = $row['fg_att'] ?? null;

	if ( ! is_numeric( $attempts ) || (int) $attempts <= 0 ) {
		return '--';
	}

	return (int) ( $row['fg_made'] ?? 0 ) . '/' . (int) $attempts;
}

/**
 * A stat table, from one spec per column.
 *
 * The spec is the point. A column's heading, its tooltip, the value in each
 * cell, and the label a phone shows when the table stacks all come from the
 * same entry, so they cannot drift apart -- the previous shape wrote the
 * heading in one loop and the cell in another, and adding a column meant
 * editing both without anything checking they matched.
 *
 * The first column is the row header. Every other cell carries `data-label`,
 * which is what the stacked layout below 640px renders in front of the value:
 * without it the numbers arrive in a column with nothing saying what they are.
 */
function trun_render_stat_table( array $columns, array $rows, string $table_class, string $caption = '', string $side = '' ): string {
	ob_start();
	?>
	<div class="trun-scroll"<?php echo $side ? ' data-side="' . esc_attr( $side ) . '"' : ''; ?>>
		<table class="trun-table <?php echo esc_attr( $table_class ); ?>">
			<?php if ( '' !== $caption ) : ?>
				<caption class="trun-table__caption"><?php echo esc_html( $caption ); ?></caption>
			<?php endif; ?>
			<thead>
				<tr>
				<?php foreach ( $columns as $column ) : ?>
					<th scope="col">
						<?php
						echo empty( $column['tip'] )
							? esc_html( $column['label'] )
							: trun_abbr( $column['label'], $column['tip'] );
						?>
					</th>
				<?php endforeach; ?>
				</tr>
			</thead>
			<tbody>
			<?php foreach ( $rows as $row ) : ?>
				<tr>
				<?php foreach ( $columns as $index => $column ) : ?>
					<?php $value = call_user_func( $column['cell'], $row ); ?>
					<?php if ( 0 === $index ) : ?>
						<th scope="row"><?php echo esc_html( $value ); ?></th>
					<?php else : ?>
						<td data-label="<?php echo esc_attr( $column['label'] ); ?>"><?php echo esc_html( $value ); ?></td>
					<?php endif; ?>
				<?php endforeach; ?>
				</tr>
			<?php endforeach; ?>
			</tbody>
		</table>
	</div>
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
			'side'  => $side,
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
 * The spread from the favorite's perspective, with movement if it moved.
 *
 * Used where one string is all there is room for: the glance table and the
 * collapsed panel summary. The odds bar splits the two halves instead, so the
 * line itself stays one short bold number.
 */
function trun_spread_text( array $game ): string {
	$spread  = trun_spread_bare( $game );
	$opening = trun_opening_text( $game );

	if ( '--' === $spread || '' === $opening ) {
		return $spread;
	}

	/* translators: 1: current line, e.g. "SEA -4.5". 2: "opened SEA -3.5". */
	return sprintf( __( '%1$s (%2$s)', 'trinity-rundown' ), $spread, $opening );
}

/** Just the line: "SEA -4.5", "PK", or a dash. */
function trun_spread_bare( array $game ): string {
	$spread = trun_get( $game, 'odds.spread', null );

	if ( null === $spread || '' === $spread ) {
		return '--';
	}

	return trim(
		trun_get( $game, 'odds.spread_favorite', '' ) . ' ' . trun_format_spread( (float) $spread )
	);
}

/**
 * "opened SEA -3.5", or nothing at all when the line has not moved.
 *
 * The opening line is written once, by WordPress, on the week's first run. A
 * line that has not moved says nothing worth the space.
 */
function trun_opening_text( array $game ): string {
	$spread      = trun_get( $game, 'odds.spread', null );
	$open_spread = trun_get( $game, 'odds.opening.spread', null );

	if ( null === $spread || null === $open_spread || '' === $open_spread ) {
		return '';
	}

	$fav      = trun_get( $game, 'odds.spread_favorite', '' );
	$open_fav = trun_get( $game, 'odds.opening.spread_favorite', $fav );

	if ( (float) $open_spread === (float) $spread && $open_fav === $fav ) {
		return '';
	}

	return sprintf(
		/* translators: %s: the opening line, e.g. "SEA -3.5" */
		__( 'opened %s', 'trinity-rundown' ),
		trim( $open_fav . ' ' . trun_format_spread( (float) $open_spread ) )
	);
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

/** Moneylines read as +142 and -170, and a missing one as a dash. */
function trun_format_moneyline( $price ): string {
	if ( null === $price || '' === $price || ! is_numeric( $price ) ) {
		return '';
	}

	$price = (int) $price;

	return ( $price > 0 ? '+' : '' ) . $price;
}

/**
 * Expose team colors to CSS as custom properties, scoped to this game.
 *
 * Primaries are not unique. Four current teams are #002244 -- Dallas, Denver,
 * New England and Seattle -- with Atlanta and Tampa Bay both #A71930 and Las
 * Vegas and Pittsburgh both #000000. Any matchup inside one of those groups
 * had two identical accents and so no accent at all, and 2026 opens with New
 * England at Seattle in the panel that renders expanded.
 *
 * On a collision the away side moves to its secondary and the home side keeps
 * its primary: the summary's left rule is the home color, and a reader
 * scanning the week by that rule should not have it shift underneath them.
 * If the secondary is missing or collides too, the side falls through to the
 * neutral declared on .trun-week rather than emitting a color at all.
 */
function trun_team_color_vars( array $game ): string {
	$away = trun_hex( trun_get( $game, 'away.color', '' ) );
	$home = trun_hex( trun_get( $game, 'home.color', '' ) );

	if ( '' !== $away && $away === $home ) {
		$alternate = trun_hex( trun_get( $game, 'away.color2', '' ) );
		$away      = ( '' !== $alternate && $alternate !== $home ) ? $alternate : '';
	}

	$vars  = '';
	$sides = [
		'away' => $away,
		'home' => $home,
	];

	foreach ( $sides as $side => $hex ) {
		if ( '' === $hex ) {
			continue;
		}
		$vars .= '--trun-' . $side . ':' . $hex . ';';
		$vars .= '--trun-' . $side . '-ink:' . trun_ink_for( $hex ) . ';';
	}

	return $vars;
}

/**
 * A six-digit hex color, lower-cased, or an empty string.
 *
 * Every value that reaches a style attribute passes through here, so nothing
 * from the payload can carry markup or a second declaration into the page.
 * Lower-casing also makes the collision test above case-insensitive, which it
 * has to be: nflverse publishes #C60C30 and #69be28 in the same column.
 */
function trun_hex( $value ): string {
	$value = strtolower( trim( (string) $value ) );

	return preg_match( '/^#[0-9a-f]{6}$/', $value ) ? $value : '';
}

/**
 * Black or white, whichever a reader can actually read on this background.
 *
 * The threshold is not 50% lightness. Contrast against white is
 * 1.05 / (L + 0.05) and against black (L + 0.05) / 0.05; the two are equal at
 * a relative luminance of 0.1791, so that is where the choice flips. Picking
 * by eye instead is how team-colored headers end up at 3:1.
 */
function trun_ink_for( string $hex ): string {
	$channels = [];

	foreach ( [ 1, 3, 5 ] as $offset ) {
		$channel    = hexdec( substr( $hex, $offset, 2 ) ) / 255;
		$channels[] = $channel <= 0.03928
			? $channel / 12.92
			: pow( ( $channel + 0.055 ) / 1.055, 2.4 );
	}

	$luminance = ( 0.2126 * $channels[0] ) + ( 0.7152 * $channels[1] ) + ( 0.0722 * $channels[2] );

	return $luminance > 0.1791 ? '#000000' : '#ffffff';
}
