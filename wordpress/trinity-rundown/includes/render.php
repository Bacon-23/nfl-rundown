<?php
/**
 * Front-end rendering.
 *
 * Everything is emitted server-side so the writeups are in the HTML for
 * search engines and for readers without JavaScript: the page is a list of
 * <details> panels and the browser does the collapsing. The only script is
 * rundown.js, which drives the expand-all control and the tabs inside each
 * game. Both render hidden until the script unhides them, so without
 * JavaScript every panel's content simply shows stacked.
 *
 * The header, odds bar and records come first, then the tabs off
 * trun_game_tabs(): the editorial sections, then the stat tables. Every stat value is a
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
			<?php echo trun_render_tabs( $game ); ?>
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
 * Everything below the records strip, grouped into tabs, in reading order.
 *
 * Each module renders independently and returns an empty string when it has
 * no data, so a missing feed costs one tab rather than the whole panel -- and
 * a tab with nothing in it is dropped rather than shown empty. The first tab
 * left standing is the one a reader sees on opening the game.
 *
 * New modules go into the tab they belong to here; nothing else changes.
 */
function trun_game_tabs( array $game ): array {
	$tabs = [
		'preview'   => [
			'label' => __( 'Rundown', 'trinity-rundown' ),
			'html'  => trun_render_notes( $game ) . trun_render_injuries( $game ),
		],
		'team'      => [
			'label' => __( 'Team', 'trinity-rundown' ),
			'html'  => trun_render_efficiency( $game ),
		],
		// The slugs follow the labels; the payload keys do not. Receivers have
		// always travelled as `passing`, and stored payloads still say so.
		// Each of the three opens with its defense-vs-position section.
		'passing'   => [
			'label' => __( 'Passing', 'trinity-rundown' ),
			'html'  => trun_render_dvp_section( $game, 'passing' ) . trun_render_quarterbacks( $game ),
		],
		'receiving' => [
			'label' => __( 'Receiving', 'trinity-rundown' ),
			'html'  => trun_render_dvp_section( $game, 'receiving' ) . trun_render_passing( $game ),
		],
		'rushing'   => [
			'label' => __( 'Rushing', 'trinity-rundown' ),
			'html'  => trun_render_dvp_section( $game, 'rushing' ) . trun_render_rushing( $game ),
		],
		'fantasy'   => [
			'label' => __( 'Fantasy', 'trinity-rundown' ),
			'html'  => trun_render_fantasy( $game ),
		],
		'kicking'   => [
			'label' => __( 'Kicking', 'trinity-rundown' ),
			'html'  => trun_render_kicking( $game ),
		],
	];

	return array_filter(
		$tabs,
		static fn( $tab ) => '' !== trim( $tab['html'] )
	);
}

/**
 * The tabs themselves.
 *
 * Every panel is in the HTML and visible: without script this is the whole
 * game stacked, exactly as it read before tabs. The tab buttons render
 * `hidden`, and rundown.js unhides them, adds the ARIA roles and hides every
 * panel but the first -- so the strip only appears once it is known to work,
 * the same contract as the expand-all control. Labels ship here rather than
 * in the script so they stay translatable.
 *
 * One tab is not a choice, so it gets no strip.
 */
function trun_render_tabs( array $game ): string {
	return trun_render_tab_group( trun_game_tabs( $game ), trun_anchor( $game ) );
}

/**
 * A tab group: the strip and its panels, from `slug => [label, html]`.
 *
 * `$anchor` prefixes every id, so a second group on the page needs one of its
 * own. rundown.js wires each group separately.
 */
function trun_render_tab_group( array $tabs, string $anchor ): string {
	if ( ! $tabs ) {
		return '';
	}

	ob_start();
	?>
	<div class="trun-tabs" data-trun-tabs>
		<?php if ( count( $tabs ) > 1 ) : ?>
			<div class="trun-tablist" hidden>
				<?php foreach ( $tabs as $slug => $tab ) : ?>
					<button type="button" class="trun-tab" id="<?php echo esc_attr( $anchor . '--tab-' . $slug ); ?>"
						data-trun-tab="<?php echo esc_attr( $slug ); ?>"
						aria-controls="<?php echo esc_attr( $anchor . '--' . $slug ); ?>">
						<?php echo esc_html( $tab['label'] ); ?>
					</button>
				<?php endforeach; ?>
			</div>
		<?php endif; ?>
		<?php foreach ( $tabs as $slug => $tab ) : ?>
			<div class="trun-tabpanel" id="<?php echo esc_attr( $anchor . '--' . $slug ); ?>"
				data-trun-panel="<?php echo esc_attr( $slug ); ?>">
				<?php
				// Already escaped by the module that built it.
				echo $tab['html'];
				?>
			</div>
		<?php endforeach; ?>
	</div>
	<?php
	return (string) ob_get_clean();
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
			'label' => __( 'PROE (rk)', 'trinity-rundown' ),
			'tip'   => __( "Pass rate over expected, against nflfastR's model. Full season. Ranked 1 to 32 across the league, most pass-heavy first.", 'trinity-rundown' ),
			'cell'  => 'trun_proe_cell',
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
 * Quarterbacks: each offense's starter, and the defense he is about to face.
 *
 * A side is the quarterback's line with the other team's defense directly
 * under it -- the same stats, what that defense allowed to every quarterback
 * it faced, ranked of 32 -- and then his line with and without a blitz. The
 * defense row is tinted like DvP; the quarterback's is not, because the row
 * beneath it already carries the read.
 *
 * The stat keys mirror `QB_STATS` in pipeline/schema.py.
 */
function trun_render_quarterbacks( array $game ): string {
	$html = '';
	foreach ( [ 'away', 'home' ] as $side ) {
		$data = trun_get( $game, 'quarterbacks.' . $side, [] );
		if ( is_array( $data ) ) {
			$html .= trun_render_qb_side( $game, $side, $data );
		}
	}

	if ( '' === $html ) {
		return '';
	}

	ob_start();
	?>
	<section class="trun-module trun-module--quarterbacks">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Passing', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'quarterbacks' ); ?>
		</h3>
		<p class="trun-module__note trun-dvp__legend">
			<span class="trun-dvp-tier trun-dvp-tier--soft"><?php esc_html_e( '1-11 easiest to pass on', 'trinity-rundown' ); ?></span>
			<span class="trun-dvp-tier trun-dvp-tier--mid"><?php esc_html_e( '12-22', 'trinity-rundown' ); ?></span>
			<span class="trun-dvp-tier trun-dvp-tier--stout"><?php esc_html_e( '23-32 hardest', 'trinity-rundown' ); ?></span>
		</p>
		<?php
		// Built from escaped parts in trun_render_qb_side().
		echo $html;
		?>
		<p class="trun-module__note trun-dvp__footnote">
			<?php esc_html_e( 'The starter is the quarterback on the roster today with the most dropbacks in the window. A dropback is a pass attempt, a sack or a scramble. The defense row is what it allowed to every quarterback it faced, ranked 1 to 32 with 1 the easiest to pass on; sack and pressure rates are ranked lowest first, so 1 still favours the offense. Scramble rate, aDOT and blitz rate are ranked by frequency, most first, and are not coloured: neither end favours the offense. Pressure is Pro Football Reference\'s count, which is per game, so there is no line under pressure -- no free source charts pressure play by play. Blitz charting is by FTN Data, licensed CC BY-SA 4.0. Both run a few days behind, so their rates count only the games charted so far.', 'trinity-rundown' ); ?>
		</p>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * One quarterback against the other side's defense: his row and the
 * defense's in one table, so each stat sits over what the defense allows,
 * then his blitz split.
 */
function trun_render_qb_side( array $game, string $side, array $data ): string {
	$quarterback = is_array( $data['quarterback'] ?? null ) ? $data['quarterback'] : null;
	$defense     = is_array( $data['defense'] ?? null ) ? $data['defense'] : null;
	$splits      = array_values( array_filter( (array) ( $data['splits'] ?? [] ), 'is_array' ) );

	if ( ! $quarterback && ! $defense ) {
		return '';
	}

	$other         = 'away' === $side ? 'home' : 'away';
	$offense_name  = (string) trun_get( $game, $side . '.name', trun_get( $game, $side . '.abbr', '' ) );
	$defense_name  = (string) trun_get( $game, $other . '.name', trun_get( $game, $other . '.abbr', '' ) );
	$defense_label = (string) trun_get( $game, $other . '.abbr', $defense_name );

	$rows = [];
	if ( $quarterback ) {
		$rows[] = [
			'name'  => [
				'text'  => (string) ( $quarterback['player'] ?? '' ),
				'aside' => __( 'QB', 'trinity-rundown' ),
			],
			'games' => $quarterback['games'] ?? null,
			'cells' => static fn( string $key, array $stat ) => call_user_func( $stat['format'], $quarterback['stats'][ $key ] ?? null ),
		];
	}
	if ( $defense ) {
		$rows[] = [
			/* translators: %s: defense team abbreviation. */
			'name'  => sprintf( __( '%s allows', 'trinity-rundown' ), $defense_label ),
			'games' => $defense['games'] ?? null,
			'cells' => static fn( string $key, array $stat ) => trun_qb_defense_cell(
				is_array( $defense['stats'][ $key ] ?? null ) ? $defense['stats'][ $key ] : [],
				$stat
			),
		];
	}

	$columns = [
		[
			'label' => '',
			'width' => '19%',
			'cell'  => static fn( $row ) => $row['name'],
		],
		[
			'label' => __( 'GP', 'trinity-rundown' ),
			'width' => '5%',
			'tip'   => __( 'Games with a dropback -- his, or the defense\'s -- over the window in the badge.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => is_numeric( $row['games'] ) ? (string) (int) $row['games'] : '--',
		],
	];

	foreach ( trun_qb_stats() as $key => $stat ) {
		$columns[] = [
			'label' => $stat['label'],
			'width' => '7.6%',
			'tip'   => $stat['tip'],
			'cell'  => static fn( $row ) => call_user_func( $row['cells'], $key, $stat ),
		];
	}

	/* translators: 1: offense team name, 2: defense team name. */
	$matchup = sprintf( __( '%1$s offense against %2$s defense', 'trinity-rundown' ), $offense_name, $defense_name );

	$html  = '<div class="trun-dvp__side"><p class="trun-dvp__matchup">' . esc_html( $matchup ) . '</p>';
	/* translators: %s: offense team name. */
	$caption = sprintf( __( '%s passing, per game', 'trinity-rundown' ), $offense_name );
	$html   .= trun_render_stat_table( $columns, $rows, 'trun-table--dvp trun-table--qb', $caption, $side );

	if ( $quarterback && $splits ) {
		$html .= trun_render_stat_table(
			trun_qb_split_columns(),
			$splits,
			'trun-table--dvp trun-table--qb-split',
			/* translators: %s: quarterback name. */
			sprintf( __( '%s against the blitz', 'trinity-rundown' ), (string) ( $quarterback['player'] ?? '' ) ),
			$side
		);
	}

	return $html . '</div>';
}

/**
 * The stat columns, in display order, with their formatting and whether the
 * rank has a direction to colour. Keys match `QB_STATS` in pipeline/schema.py.
 */
function trun_qb_stats(): array {
	$rate  = static fn( $value ) => trun_percent( $value, 1 );
	$tenth = static fn( $value ) => trun_decimal( $value, 1 );

	return [
		'att'           => [
			'label'  => __( 'Att', 'trinity-rundown' ),
			'tip'    => __( 'Pass attempts per game. Sacks, scrambles and spikes are not attempts.', 'trinity-rundown' ),
			'format' => $tenth,
		],
		'dropbacks'     => [
			'label'  => __( 'DB', 'trinity-rundown' ),
			'tip'    => __( 'Dropbacks per game: pass attempts, sacks and scrambles. Two-point tries are not counted.', 'trinity-rundown' ),
			'format' => $tenth,
		],
		'sack_rate'     => [
			'label'  => __( 'Sack%', 'trinity-rundown' ),
			'tip'    => __( 'Sacks per dropback. For the defense, ranked lowest first, so 1 still favours the offense.', 'trinity-rundown' ),
			'format' => $rate,
		],
		'scramble_rate' => [
			'label'   => __( 'Scr%', 'trinity-rundown' ),
			'tip'     => __( 'Scrambles per dropback: a called pass the quarterback ran with. Ranked by frequency and not coloured.', 'trinity-rundown' ),
			'format'  => $rate,
			'neutral' => true,
		],
		'adot'          => [
			'label'   => __( 'aDOT', 'trinity-rundown' ),
			'tip'     => __( 'Average depth of target: air yards per pass attempt, measured from the line of scrimmage. Ranked by depth and not coloured.', 'trinity-rundown' ),
			'format'  => $tenth,
			'neutral' => true,
		],
		'cpoe'          => [
			'label'  => __( 'CPOE', 'trinity-rundown' ),
			'tip'    => __( 'Completion percentage over expected, in percentage points, from nflfastR\'s model of how often each throw is caught.', 'trinity-rundown' ),
			'format' => static fn( $value ) => trun_signed( $value, 1 ),
		],
		'cmp_pct'       => [
			'label'  => __( 'Cmp%', 'trinity-rundown' ),
			'tip'    => __( 'Completions per pass attempt.', 'trinity-rundown' ),
			'format' => $rate,
		],
		'ypa'           => [
			'label'  => __( 'YPA', 'trinity-rundown' ),
			'tip'    => __( 'Passing yards per attempt. Sack yardage is not taken off.', 'trinity-rundown' ),
			'format' => $tenth,
		],
		'pressure_rate' => [
			'label'  => __( 'Press%', 'trinity-rundown' ),
			'tip'    => __( 'Pressures per dropback, from Pro Football Reference\'s charting: hurries, hits and sacks. For the quarterback, how often he was pressured; for the defense, how often it got there, ranked lowest first. Counts only the games PFR has charted.', 'trinity-rundown' ),
			'format' => $rate,
		],
		'blitz_rate'    => [
			'label'   => __( 'Blitz%', 'trinity-rundown' ),
			'tip'     => __( 'Dropbacks with at least one blitzer, from FTN\'s charting. For the quarterback, how often he was blitzed; for the defense, how often it blitzed. Ranked by frequency and not coloured. Counts only the plays FTN has charted.', 'trinity-rundown' ),
			'format'  => $rate,
			'neutral' => true,
		],
	];
}

/**
 * A defense's value with its rank beside it. Tinted by band unless the stat
 * has no direction, in which case the rank alone is printed.
 */
function trun_qb_defense_cell( array $cell, array $stat ): array {
	$out  = [ 'text' => call_user_func( $stat['format'], $cell['value'] ?? null ) ];
	$rank = $cell['rank'] ?? null;

	if ( null !== $rank && is_numeric( $rank ) ) {
		$out['aside'] = (string) (int) $rank;
		if ( empty( $stat['neutral'] ) ) {
			$out['class'] = 'trun-dvp-tier trun-dvp-tier--' . trun_dvp_tier( (int) $rank );
		}
	}

	return $out;
}

/** The blitz split's columns. */
function trun_qb_split_columns(): array {
	$labels = [
		'blitz'    => __( 'Blitzed', 'trinity-rundown' ),
		'no_blitz' => __( 'Not blitzed', 'trinity-rundown' ),
	];

	return [
		[
			'label' => '',
			'width' => '24%',
			'cell'  => static fn( $row ) => $labels[ (string) ( $row['split'] ?? '' ) ] ?? (string) ( $row['split'] ?? '' ),
		],
		[
			'label' => __( 'Dropbacks', 'trinity-rundown' ),
			'width' => '19%',
			'tip'   => __( 'His dropbacks FTN has charted, with and without a blitz. Totals, not per game.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => is_numeric( $row['dropbacks'] ?? null ) ? (string) (int) $row['dropbacks'] : '--',
		],
		[
			'label' => __( 'Cmp%', 'trinity-rundown' ),
			'width' => '19%',
			'tip'   => __( 'Completions per pass attempt on those dropbacks.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['cmp_pct'] ?? null, 1 ),
		],
		[
			'label' => __( 'YPA', 'trinity-rundown' ),
			'width' => '19%',
			'tip'   => __( 'Passing yards per attempt on those dropbacks.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['ypa'] ?? null, 1 ),
		],
		[
			'label' => __( 'Sack%', 'trinity-rundown' ),
			'width' => '19%',
			'tip'   => __( 'Sacks per dropback on those dropbacks.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['sack_rate'] ?? null, 1 ),
		],
	];
}

/**
 * Receiving, one table per side. Named `passing` in the payload, where it has
 * always been.
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

	ob_start();
	?>
	<section class="trun-module trun-module--passing">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Receiving', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'passing' ); ?>
		</h3>
		<?php foreach ( $sides as $side ) : ?>
			<?php
			$weeks   = trun_get( $game, 'passing.' . $side['side'] . '_weeks', [] );
			$columns = trun_passing_columns( is_array( $weeks ) ? array_values( $weeks ) : [] );
			echo trun_render_stat_table( $columns, $side['rows'], 'trun-table--passing', $side['label'], $side['side'] );
			?>
		<?php endforeach; ?>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * The passing table's columns for one side.
 *
 * Built per side because the week columns are that team's last games, byes
 * skipped, so the two tables in one matchup can show different weeks. With no
 * weeks -- a payload from before the week columns existed, which storage can
 * carry forward, or a run where the weekly feeds failed -- this is the season
 * table exactly as it was.
 */
function trun_passing_columns( array $weeks ): array {
	$weeks = array_slice( $weeks, -4 );
	$count = count( $weeks );

	// Season-only widths, and the widths once four week columns and L4 have
	// squeezed in. Fewer than four weeks hands the spare room to the name.
	// The two scoring-area columns hold "7 (24%)" and are the widest numbers
	// in the row, so they keep their width in both layouts.
	$player_width = $count ? 16 + ( 4 - $count ) * 6 : 28;

	$columns = [
		[
			'label' => __( 'Player', 'trinity-rundown' ),
			'width' => $player_width . '%',
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Role', 'trinity-rundown' ),
			'width' => $count ? '7%' : '10%',
			'tip'   => __( 'Numbered within position across the whole team, and assigned before the five-row cut -- so a team\'s WR3 is its third receiver, not the third name left in the table.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['role'] ?? '' ),
		],
		[
			'label' => __( 'Tgt share', 'trinity-rundown' ),
			'width' => $count ? '8%' : '13%',
			'tip'   => __( 'Player targets divided by team targets, season to date.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['target_share'] ?? null, 1 ),
		],
	];

	foreach ( $weeks as $index => $week ) {
		$columns[] = [
			/* translators: %d: NFL week number. */
			'label' => sprintf( __( 'Wk %d', 'trinity-rundown' ), (int) $week ),
			'width' => '6%',
			'tip'   => __( 'Share of team targets that week, measured against the team he played for that week. A dash means he did not play; 0% means he played and was not targeted.', 'trinity-rundown' ),
			'cell'  => static function ( $row ) use ( $index ) {
				$cells = isset( $row['weekly_share'] ) && is_array( $row['weekly_share'] ) ? $row['weekly_share'] : [];
				return trun_percent( $cells[ $index ] ?? null );
			},
		];
	}

	if ( $count ) {
		$columns[] = [
			'label' => __( 'L4', 'trinity-rundown' ),
			'width' => '7%',
			'tip'   => __( 'Targets divided by team targets across the weeks shown, counting only the weeks he played -- so a receiver back from injury is judged on the games he was in.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['l4_share'] ?? null, 1 ),
		];
	}

	$columns[] = [
		// The one heading on the page that would mislead without its tooltip.
		'label' => __( 'Tgt rate', 'trinity-rundown' ),
		'width' => $count ? '7%' : '13%',
		'tip'   => __( 'Targets per estimated pass snap -- a proxy for TPRR, which requires charted route data. It reads high against a true TPRR figure; the ranking is sound, the level is not comparable.', 'trinity-rundown' ),
		'cell'  => static fn( $row ) => trun_percent( $row['target_rate'] ?? null, 1 ),
	];
	$columns[] = [
		'label' => __( 'Rec yds/gm', 'trinity-rundown' ),
		'width' => $count ? '9%' : '14%',
		'tip'   => __( 'Receiving yards divided by games with at least one offensive snap, so weeks missed entirely do not drag the average down.', 'trinity-rundown' ),
		'cell'  => static fn( $row ) => trun_decimal( $row['rec_yds_per_game'] ?? null, 1 ),
	];
	$columns[] = [
		'label' => __( 'RZ tgt', 'trinity-rundown' ),
		'width' => '11%',
		'tip'   => __( 'Targets from the opponent\'s 20-yard line or closer, season to date, and his share of the team\'s. Two-point tries are not counted.', 'trinity-rundown' ),
		'cell'  => static fn( $row ) => trun_count_share( $row['rz_targets'] ?? null, $row['rz_target_share'] ?? null ),
	];
	$columns[] = [
		'label' => __( 'EZ tgt', 'trinity-rundown' ),
		'width' => '11%',
		'tip'   => __( 'Targets whose air yards reach the goal line, from anywhere on the field, and his share of the team\'s. Play-by-play has no end-zone flag, so this is a proxy: a pass thrown to the goal line itself counts.', 'trinity-rundown' ),
		'cell'  => static fn( $row ) => trun_count_share( $row['ez_targets'] ?? null, $row['ez_target_share'] ?? null ),
	];

	return $columns;
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
			'width' => '28%',
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Snap %', 'trinity-rundown' ),
			'width' => '13%',
			'tip'   => __( 'Pro Football Reference offensive snap share, averaged over games the player appeared in rather than over the season. The table is sorted on this.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['snap_share'] ?? null ),
		],
		[
			'label' => __( 'Rush att/gm', 'trinity-rundown' ),
			'width' => '15%',
			'tip'   => __( 'Rushing attempts divided by games with a snap. A back needs one attempt a game to appear here at all.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['rush_att_per_game'] ?? null, 1 ),
		],
		[
			'label' => __( 'Tgt share', 'trinity-rundown' ),
			'width' => '13%',
			'tip'   => __( 'Player targets divided by team targets, season to date. The same figure, from the same code, as in the receiving table.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['target_share'] ?? null, 1 ),
		],
		[
			'label' => __( 'Yds/att', 'trinity-rundown' ),
			'width' => '13%',
			'tip'   => __( 'Rushing yards divided by rushing attempts.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['yards_per_att'] ?? null, 1 ),
		],
		[
			'label' => __( 'Inside 5', 'trinity-rundown' ),
			'width' => '18%',
			'tip'   => __( 'Designed runs from the opponent\'s 5-yard line or closer, season to date, and his share of the team\'s. Scrambles and kneels are not counted; quarterback sneaks count toward the team total.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_count_share( $row['inside5_carries'] ?? null, $row['inside5_share'] ?? null ),
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
 * Defense vs. position: what each defense gives up, set against the offense
 * about to face it.
 *
 * One section -- passing, receiving or rushing -- at the top of the game tab
 * of the same name, and in it both offenses. A side is two tables: what the
 * *other* team's defense allows per game at each role, with its rank of 32,
 * and then this offense's players with their own per-game lines. A player's
 * cell is coloured by the rank the defense holds at his role, which is the
 * read the section exists for.
 *
 * The section and stat keys mirror `DVP_SECTIONS` in pipeline/schema.py.
 */
function trun_render_dvp_section( array $game, string $key ): string {
	$section = trun_dvp_sections()[ $key ] ?? null;
	if ( ! $section ) {
		return '';
	}

	$blocks = '';
	foreach ( [ 'away', 'home' ] as $side ) {
		$data = trun_get( $game, 'dvp.' . $side . '.' . $key, [] );
		if ( is_array( $data ) ) {
			$blocks .= trun_render_dvp_side( $game, $side, $data, $section );
		}
	}

	if ( '' === $blocks ) {
		return '';
	}

	ob_start();
	?>
	<section class="trun-module trun-module--dvp">
		<h3 class="trun-module__heading">
			<?php esc_html_e( 'Defense vs. Position', 'trinity-rundown' ); ?>
			<?php echo trun_render_badge( $game, 'dvp' ); ?>
		</h3>
		<p class="trun-module__note trun-dvp__legend">
			<span class="trun-dvp-tier trun-dvp-tier--soft"><?php esc_html_e( '1-11 gives up the most', 'trinity-rundown' ); ?></span>
			<span class="trun-dvp-tier trun-dvp-tier--mid"><?php esc_html_e( '12-22', 'trinity-rundown' ); ?></span>
			<span class="trun-dvp-tier trun-dvp-tier--stout"><?php esc_html_e( '23-32 gives up the least', 'trinity-rundown' ); ?></span>
		</p>
		<?php
		// Built from escaped parts above.
		echo $blocks;
		?>
		<p class="trun-module__note trun-dvp__footnote">
			<?php esc_html_e( 'Allowed figures are what the defense gave up per game to every opponent at that role, combined, ranked 1 to 32 with 1 giving up the most. Interceptions are ranked the other way round, so 1 always favours the offense. Player lines are his own per game, and each cell is coloured by what this defense allows to his role; a fullback counts as a running back. Long is the longest gain in each game, averaged -- not the season\'s longest play. A red zone carry is a designed run, so scrambles are not counted.', 'trinity-rundown' ); ?>
		</p>
	</section>
	<?php
	return (string) ob_get_clean();
}

/**
 * One offense against the other side's defense, within one section.
 *
 * The stat columns of the two tables line up: the defense table's Role column
 * is exactly as wide as the player table's name, games and targets columns
 * together, so a player's yards sit under the yards his opponent allows.
 */
function trun_render_dvp_side( array $game, string $side, array $data, array $section ): string {
	$allows  = array_values( array_filter( (array) ( $data['allows'] ?? [] ), 'is_array' ) );
	$players = array_values( array_filter( (array) ( $data['players'] ?? [] ), 'is_array' ) );

	if ( ! $allows && ! $players ) {
		return '';
	}

	$other   = 'away' === $side ? 'home' : 'away';
	$offense = (string) trun_get( $game, $side . '.name', trun_get( $game, $side . '.abbr', '' ) );
	$defense = (string) trun_get( $game, $other . '.name', trun_get( $game, $other . '.abbr', '' ) );

	// The defense's rank at each role, which is what colours a player's cells.
	$by_role = [];
	foreach ( $allows as $row ) {
		$by_role[ (string) ( $row['role'] ?? '' ) ] = is_array( $row['stats'] ?? null ) ? $row['stats'] : [];
	}

	// Receiving carries a targets column, so its stats give up a little width
	// to keep the name column from wrapping every row onto three lines.
	$with_targets  = isset( $section['stats']['rec'] );
	$stat_width    = $with_targets ? '11%' : '12%';
	$allow_columns = [
		[
			'label' => __( 'Role', 'trinity-rundown' ),
			'width' => $with_targets ? '34%' : '28%',
			'cell'  => static fn( $row ) => trun_dvp_role_label( (string) ( $row['role'] ?? '' ) ),
		],
	];

	$player_columns = [
		[
			'label' => __( 'Player', 'trinity-rundown' ),
			'width' => $with_targets ? '23%' : '21%',
			'cell'  => static fn( $row ) => [
				'text'  => (string) ( $row['player'] ?? '' ),
				'aside' => (string) ( $row['position'] ?? '' ),
			],
		],
		[
			'label' => __( 'GP', 'trinity-rundown' ),
			'width' => $with_targets ? '5%' : '7%',
			'tip'   => __( 'Games he recorded a stat in, over the window in the badge.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => isset( $row['games'] ) ? (string) (int) $row['games'] : '--',
		],
	];

	if ( $with_targets ) {
		$player_columns[] = [
			'label' => __( 'Tgt', 'trinity-rundown' ),
			'width' => '6%',
			'tip'   => __( 'Targets per game. The list is sorted on this.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['stats']['tgt'] ?? null, 1 ),
		];
	}

	foreach ( $section['stats'] as $key => $stat ) {
		$allow_columns[] = [
			'label' => $stat['label'],
			'width' => $stat_width,
			'tip'   => $stat['tip'],
			'cell'  => static fn( $row ) => trun_dvp_cell(
				$row['stats'][ $key ]['value'] ?? null,
				$row['stats'][ $key ]['rank'] ?? null
			),
		];

		$player_columns[] = [
			'label' => $stat['label'],
			'width' => $stat_width,
			'tip'   => $stat['tip'],
			'cell'  => static fn( $row ) => trun_dvp_cell(
				$row['stats'][ $key ] ?? null,
				$by_role[ (string) ( $row['role'] ?? '' ) ][ $key ]['rank'] ?? null
			),
		];
	}

	/* translators: 1: offense team name, 2: defense team name. */
	$matchup = sprintf( __( '%1$s offense against %2$s defense', 'trinity-rundown' ), $offense, $defense );

	$html = '<div class="trun-dvp__side"><p class="trun-dvp__matchup">' . esc_html( $matchup ) . '</p>';

	if ( $allows ) {
		/* translators: %s: defense team name. */
		$caption = sprintf( __( '%s allow, per game', 'trinity-rundown' ), $defense );
		$html   .= trun_render_stat_table( $allow_columns, $allows, 'trun-table--dvp', $caption, $other );
	}

	if ( $players ) {
		/* translators: %s: offense team name. */
		$caption = sprintf( __( '%s players, per game', 'trinity-rundown' ), $offense );
		$html   .= trun_render_stat_table( $player_columns, $players, 'trun-table--dvp', $caption, $side );
	}

	return $html . '</div>';
}

/**
 * The three sections, their stat columns in display order, and what each
 * column means. Keys match `DVP_SECTIONS` in pipeline/schema.py.
 */
function trun_dvp_sections(): array {
	$ppr = [
		'label' => __( 'PPR', 'trinity-rundown' ),
		'tip'   => __( 'Full-PPR fantasy points per game, as nflverse scores them. It is the role\'s whole total, so a running back\'s catches and carries are both in it, and the same figure appears under Receiving and Rushing.', 'trinity-rundown' ),
	];

	return [
		'passing'   => [
			'label' => __( 'Passing', 'trinity-rundown' ),
			'stats' => [
				'pass_yds' => [
					'label' => __( 'Pass yds', 'trinity-rundown' ),
					'tip'   => __( 'Passing yards per game.', 'trinity-rundown' ),
				],
				'comp'     => [
					'label' => __( 'Comp', 'trinity-rundown' ),
					'tip'   => __( 'Completions per game.', 'trinity-rundown' ),
				],
				'att'      => [
					'label' => __( 'Att', 'trinity-rundown' ),
					'tip'   => __( 'Pass attempts per game.', 'trinity-rundown' ),
				],
				'pass_td'  => [
					'label' => __( 'Pass TD', 'trinity-rundown' ),
					'tip'   => __( 'Passing touchdowns per game.', 'trinity-rundown' ),
				],
				'int'      => [
					'label' => __( 'INT', 'trinity-rundown' ),
					'tip'   => __( 'Interceptions per game. Ranked the other way round: 1 is the defense that picks off the fewest, so 1 still favours the offense.', 'trinity-rundown' ),
				],
				'ppr'      => $ppr,
			],
		],
		'receiving' => [
			'label' => __( 'Receiving', 'trinity-rundown' ),
			'stats' => [
				'rec'      => [
					'label' => __( 'Rec', 'trinity-rundown' ),
					'tip'   => __( 'Receptions per game.', 'trinity-rundown' ),
				],
				'rec_yds'  => [
					'label' => __( 'Rec yds', 'trinity-rundown' ),
					'tip'   => __( 'Receiving yards per game.', 'trinity-rundown' ),
				],
				'rec_td'   => [
					'label' => __( 'Rec TD', 'trinity-rundown' ),
					'tip'   => __( 'Receiving touchdowns per game.', 'trinity-rundown' ),
				],
				'rz_tgt'   => [
					'label' => __( 'RZ tgt', 'trinity-rundown' ),
					'tip'   => __( 'Targets from the opponent\'s 20-yard line or closer, per game. Two-point tries are not counted.', 'trinity-rundown' ),
				],
				'long_rec' => [
					'label' => __( 'Long', 'trinity-rundown' ),
					'tip'   => __( 'The longest reception in each game, averaged over games -- the number a Longest Reception prop prices, not the season\'s longest play.', 'trinity-rundown' ),
				],
				'ppr'      => $ppr,
			],
		],
		'rushing'   => [
			'label' => __( 'Rushing', 'trinity-rundown' ),
			'stats' => [
				'carries'   => [
					'label' => __( 'Car', 'trinity-rundown' ),
					'tip'   => __( 'Carries per game, scrambles included, as the box score counts them.', 'trinity-rundown' ),
				],
				'rush_yds'  => [
					'label' => __( 'Rush yds', 'trinity-rundown' ),
					'tip'   => __( 'Rushing yards per game.', 'trinity-rundown' ),
				],
				'rush_td'   => [
					'label' => __( 'Rush TD', 'trinity-rundown' ),
					'tip'   => __( 'Rushing touchdowns per game.', 'trinity-rundown' ),
				],
				'rz_car'    => [
					'label' => __( 'RZ car', 'trinity-rundown' ),
					'tip'   => __( 'Designed runs from the opponent\'s 20-yard line or closer, per game. Scrambles and kneels are not counted; quarterback sneaks are.', 'trinity-rundown' ),
				],
				'long_rush' => [
					'label' => __( 'Long', 'trinity-rundown' ),
					'tip'   => __( 'The longest run in each game, scrambles included, averaged over games -- not the season\'s longest play.', 'trinity-rundown' ),
				],
				'ppr'       => $ppr,
			],
		],
	];
}

/** "All WRs" rather than "WR": the row is every receiver who faced them. */
function trun_dvp_role_label( string $role ): string {
	$labels = [
		'QB' => __( 'QB', 'trinity-rundown' ),
		'WR' => __( 'All WRs', 'trinity-rundown' ),
		'TE' => __( 'All TEs', 'trinity-rundown' ),
		'RB' => __( 'All RBs', 'trinity-rundown' ),
	];

	return $labels[ $role ] ?? $role;
}

/**
 * A value with the defense's rank beside it, tinted by band.
 *
 * The rank is always printed, so the band never rests on colour alone. No
 * rank -- a player whose role the defense table lacks -- is the value alone.
 */
function trun_dvp_cell( $value, $rank ): array {
	$cell = [ 'text' => trun_decimal( $value, 1 ) ];

	if ( null !== $rank && is_numeric( $rank ) ) {
		$cell['aside'] = (string) (int) $rank;
		$cell['class'] = 'trun-dvp-tier trun-dvp-tier--' . trun_dvp_tier( (int) $rank );
	}

	return $cell;
}

/** 1-11 gives up the most, 23-32 the least; the middle third is neutral. */
function trun_dvp_tier( int $rank ): string {
	if ( $rank <= 11 ) {
		return 'soft';
	}

	return $rank <= 22 ? 'mid' : 'stout';
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
			'width' => '30%',
			'cell'  => static fn( $row ) => (string) ( $row['player'] ?? '' ),
		],
		[
			'label' => __( 'Pos', 'trinity-rundown' ),
			'width' => '10%',
			'tip'   => __( 'The quarterback is pinned to the top of each table rather than ranked into it. On raw PPR he outscores his own receivers on almost every team, so ranking him would cost a skill-player row and tell you nothing.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => (string) ( $row['position'] ?? '' ),
		],
		[
			'label' => __( 'PPR/gm', 'trinity-rundown' ),
			'width' => '16%',
			'tip'   => __( 'Full PPR as nflverse scores it: one point per reception, one per 25 passing yards, four for a passing touchdown, a tenth per rushing and receiving yard. Averaged over games played, not weeks elapsed.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['ppr_per_game'] ?? null, 1 ),
		],
		[
			'label' => __( 'Home', 'trinity-rundown' ),
			'width' => '15%',
			'tip'   => $window,
			'cell'  => static fn( $row ) => trun_venue_cell( $row, 'home' ),
		],
		[
			'label' => __( 'Away', 'trinity-rundown' ),
			'width' => '14%',
			'tip'   => $window,
			'cell'  => static fn( $row ) => trun_venue_cell( $row, 'away' ),
		],
		[
			'label' => __( 'Split', 'trinity-rundown' ),
			'width' => '15%',
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
			'width' => '30%',
			'cell'  => static fn( $row ) => 'home' === ( $row['venue'] ?? '' )
				? __( 'At home', 'trinity-rundown' )
				: __( 'On the road', 'trinity-rundown' ),
		],
		[
			'label' => __( 'FG', 'trinity-rundown' ),
			'width' => '16%',
			'tip'   => __( 'Field goals made and attempted at that venue over the kicker\'s last 17 games. Blocks count as attempts, the way every kicking table counts them.', 'trinity-rundown' ),
			'cell'  => 'trun_fg_cell',
		],
		[
			'label' => __( 'FG%', 'trinity-rundown' ),
			'width' => '18%',
			'tip'   => __( 'Made divided by attempted at that venue. A kicker needs five attempts across the whole window before he appears at all: two-for-two is not a hundred percent of anything.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_percent( $row['fg_pct'] ?? null, 1 ),
		],
		[
			'label' => __( 'Long', 'trinity-rundown' ),
			'width' => '16%',
			'tip'   => __( 'Longest field goal made at that venue during the window.', 'trinity-rundown' ),
			'cell'  => static fn( $row ) => trun_decimal( $row['fg_long'] ?? null, 0 ),
		],
		[
			'label' => __( 'Att/gm', 'trinity-rundown' ),
			'width' => '20%',
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
 *
 * A column may also declare a `width`, and that is what keeps a module's two
 * tables -- one per team -- on the same grid. Left to itself a table sizes its
 * columns from its own content, so the home side's numbers landed nowhere near
 * the away side's above them. A spec that declares no width keeps automatic
 * sizing: the single-table modules have nothing to line up with, and their
 * headings are too long to survive a fixed share of the width.
 *
 * The width goes on the header cell rather than into a <colgroup>, which is
 * not a style preference. Below 640px the rows stop being table rows, and a
 * colgroup then applies its first width to the one anonymous column the
 * browser makes of them -- every stacked card squeezed to 30% of the panel.
 * The header row is display:none there, so widths written on it go away
 * exactly when the fixed layout they feed does.
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
					<th scope="col"<?php echo empty( $column['width'] ) ? '' : ' style="width: ' . esc_attr( $column['width'] ) . ';"'; ?>>
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
					<?php
					$value = call_user_func( $column['cell'], $row );
					$class = is_array( $value ) && ! empty( $value['class'] ) ? ' class="' . esc_attr( $value['class'] ) . '"' : '';
					?>
					<?php if ( 0 === $index ) : ?>
						<th scope="row"<?php echo $class; ?>><?php echo trun_cell_html( $value ); ?></th>
					<?php else : ?>
						<td data-label="<?php echo esc_attr( $column['label'] ); ?>"<?php echo $class; ?>><?php echo trun_cell_html( $value ); ?></td>
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
 * One cell's markup, escaped.
 *
 * A column's `cell` callback returns a plain string, which is most of them, or
 * `[ 'text' => ..., 'aside' => ..., 'class' => ... ]` when the value needs
 * something beside it -- a DvP rank chip, a player's position. Both parts are
 * escaped here, so no callback ever hands this function raw HTML.
 */
function trun_cell_html( $value ): string {
	if ( ! is_array( $value ) ) {
		return esc_html( (string) $value );
	}

	$html = esc_html( (string) ( $value['text'] ?? '' ) );

	if ( isset( $value['aside'] ) && '' !== (string) $value['aside'] ) {
		$html .= ' <span class="trun-cell__aside">' . esc_html( (string) $value['aside'] ) . '</span>';
	}

	// One wrapper, so a stacked card's flex row keeps the value and its aside
	// together on the right rather than spreading them across the width.
	return '<span>' . $html . '</span>';
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
		'scouting'     => __( 'Trinity Notes', 'trinity-rundown' ),
		'player_props' => __( 'Top Player Props', 'trinity-rundown' ),
		'side_total'   => __( 'Side/Total Leans', 'trinity-rundown' ),
		'td_leans'     => __( 'Anytime TD Leans', 'trinity-rundown' ),
		'prediction'   => __( 'Score Prediction', 'trinity-rundown' ),
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
 *
 * The odds credit describes the slate, not the first game on it. A game the
 * book has taken down -- Thursday's, by Friday -- falls back to the nflverse
 * line on its own, which is expected and not worth a page-wide notice. So the
 * book is named if any game carries its lines, and the fallback is only
 * announced when every game fell back, which means the Odds API itself failed.
 */
function trun_render_footer( array $games ): string {
	$first = $games[0] ?? [];
	$as_of = trun_get( $first, '_meta.updated_at', '' );

	$credited = $first;
	$live     = false;
	foreach ( $games as $game ) {
		if ( 'odds_api' === trun_get( $game, 'odds.source', '' ) ) {
			$credited = $game;
			$live     = true;
			break;
		}
	}

	$book  = trun_get( $credited, 'odds.book_label', '' );
	$parts = [];
	if ( $book ) {
		/* translators: %s: sportsbook the lines were taken from, e.g. DraftKings. */
		$parts[] = sprintf( __( 'Odds: %s', 'trinity-rundown' ), $book );
	}
	if ( ! $live && 'nflverse_fallback' === trun_get( $first, 'odds.source', '' ) ) {
		$parts[] = __( 'consensus fallback in use', 'trinity-rundown' );
	}
	// Stored in UTC; shown in Eastern, the zone every kickoff on the page is
	// already in. "ET" rather than "EST", since New York is on daylight time
	// for most of the season and the zone below follows it.
	$as_of_time = $as_of ? strtotime( $as_of . ' UTC' ) : false;
	if ( $as_of_time ) {
		$parts[] = sprintf(
			/* translators: %s: date and time of the last stats refresh, US Eastern. */
			__( 'Stats as of %s ET', 'trinity-rundown' ),
			wp_date( 'M j, Y g:i a', $as_of_time, new DateTimeZone( 'America/New_York' ) )
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
 * A count and the team share behind it, e.g. "7 (24%)".
 *
 * A missing count is a dash: a payload from before these columns existed has
 * no count, and that is not the same as a player with none. A count with no
 * share -- a team that never got there -- shows the count alone.
 */
function trun_count_share( $count, $share ): string {
	if ( null === $count || '' === $count || ! is_numeric( $count ) ) {
		return '--';
	}

	$text = number_format( (float) $count );

	if ( null !== $share && '' !== $share && is_numeric( $share ) ) {
		$text .= ' (' . trun_percent( $share ) . ')';
	}

	return $text;
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

/**
 * The PROE cell: the number and where it ranks, e.g. "+2.9% (4th)".
 *
 * 1st is the most pass-heavy offense, which is a tendency rather than a grade.
 * A payload from before the rank existed shows the number alone.
 */
function trun_proe_cell( array $row ): string {
	$text = trun_percent( $row['proe'] ?? null, 1, true );

	if ( '--' === $text ) {
		return $text;
	}

	$rank = $row['proe_rank'] ?? null;

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
 *
 * Near-black primaries have already moved to their secondary by then -- see
 * trun_side_color() -- so Las Vegas at Pittsburgh is silver against gold and
 * never reaches the collision rule at all.
 */
function trun_team_color_vars( array $game ): string {
	$away = trun_side_color( $game, 'away' );
	$home = trun_side_color( $game, 'home' );

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
	return trun_luminance( $hex ) > 0.1791 ? '#000000' : '#ffffff';
}

/**
 * WCAG relative luminance of a six-digit hex colour: 0 for black, 1 for white.
 */
function trun_luminance( string $hex ): float {
	$channels = [];

	foreach ( [ 1, 3, 5 ] as $offset ) {
		$channel    = hexdec( substr( $hex, $offset, 2 ) ) / 255;
		$channels[] = $channel <= 0.03928
			? $channel / 12.92
			: pow( ( $channel + 0.055 ) / 1.055, 2.4 );
	}

	return ( 0.2126 * $channels[0] ) + ( 0.7152 * $channels[1] ) + ( 0.0722 * $channels[2] );
}

/**
 * A side's colour, moved to its secondary when the primary is all but black.
 *
 * Trinity Analytics' theme is black, and Las Vegas and Pittsburgh are
 * #000000 and Chicago #0B162A: their card edge, table captions and selected
 * tab were black on black. Below a luminance of 0.01 -- 1.2:1 against black,
 * which takes exactly those three -- the secondary is used instead: silver,
 * gold, orange. The navy teams (#002244 and the like) sit at 1.3:1 and up;
 * they are dim but visible, and still look like themselves, so they stay.
 *
 * A secondary that is missing, or no lighter, leaves the primary alone.
 */
function trun_side_color( array $game, string $side ): string {
	$primary = trun_hex( trun_get( $game, $side . '.color', '' ) );

	if ( '' === $primary || trun_luminance( $primary ) >= 0.01 ) {
		return $primary;
	}

	$secondary = trun_hex( trun_get( $game, $side . '.color2', '' ) );

	return ( '' !== $secondary && trun_luminance( $secondary ) > trun_luminance( $primary ) )
		? $secondary
		: $primary;
}
