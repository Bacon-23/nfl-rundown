<?php
/**
 * Render the front end locally, without WordPress.
 *
 * The plugin's rendering had no way to be seen short of deploying to staging,
 * which made every visual change a round trip. This loads the *real*
 * storage.php and render.php against a payload from `build/*.json` and writes
 * a standalone HTML file, so markup and layout can be checked in a browser
 * before anything is pushed.
 *
 * It fakes the database, not the code under test: rows are built by hand and
 * handed to TRUN_Storage::view_row(), so the merge order -- stats, overrides,
 * notes, opening line -- is exercised exactly as it is on the site.
 *
 * There is no PHP on this dev machine; run it in the container CI uses. From
 * Git Bash both flags are required, or the mount silently resolves to nothing:
 *
 *   MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src php:8.2-cli \
 *     php tools/preview.php build/2026-week-01.json build/preview.html
 *
 * The output links the real stylesheet rather than inlining it, so editing
 * rundown.css and reloading the page is enough -- no re-run.
 *
 * Usage:
 *   php tools/preview.php <payload.json> [out.html] [options]
 *
 *   --notes           attach sample editorial copy to every game, so the
 *                     Scouting Notes / TD Leans / Score Prediction sections
 *                     render. Off by default: those are the writer's, and an
 *                     unwritten week is the honest default view.
 *   --game=<needle>   render only games whose id contains <needle>, e.g.
 *                     --game=NE_SEA. Faster to iterate on one panel.
 *   --locked          render as a published week, out of published_json.
 *   --dark            render the shell dark. Trinity Analytics' theme is dark
 *                     unconditionally -- not by OS preference -- and checking
 *                     only the light shell is how a `background: Canvas` that
 *                     put white boxes behind near-white text reached the live
 *                     site. Check both.
 */

if ( 'cli' !== PHP_SAPI ) {
	exit( 1 );
}

define( 'ABSPATH', __DIR__ . '/' );
define( 'TRUN_VERSION', 'preview' );

/* -------------------------------------------------------------------------
 * The WordPress surface render.php and storage.php actually touch.
 *
 * Deliberately thin. These are not reimplementations -- they are just enough
 * for the real files to run. Anything subtler than escaping belongs in a test
 * rather than here.
 * ---------------------------------------------------------------------- */

function esc_html( $text ) {
	return htmlspecialchars( (string) $text, ENT_QUOTES, 'UTF-8' );
}

function esc_attr( $text ) {
	return htmlspecialchars( (string) $text, ENT_QUOTES, 'UTF-8' );
}

function esc_url( $url ) {
	$url = trim( (string) $url );
	return preg_match( '#^https?://#i', $url ) ? esc_attr( $url ) : '';
}

function esc_html_e( $text, $domain = null ) {
	echo esc_html( $text );
}

function __( $text, $domain = null ) {
	return $text;
}

function sanitize_title( $title ) {
	$slug = preg_replace( '/[^a-z0-9]+/', '-', strtolower( (string) $title ) );
	return trim( (string) $slug, '-' );
}

function wp_kses_post( $html ) {
	// The preview trusts its own sample copy. The real filter is WordPress's.
	return (string) $html;
}

function wpautop( $text ) {
	$blocks = preg_split( '/\n\s*\n/', trim( (string) $text ) );

	return implode(
		'',
		array_map(
			static function ( $block ) {
				return '<p>' . nl2br( trim( $block ) ) . '</p>';
			},
			$blocks
		)
	);
}

function mysql2date( $format, $date ) {
	$time = strtotime( (string) $date );
	return false === $time ? (string) $date : gmdate( $format, $time );
}

function current_time( $type, $gmt = 0 ) {
	return gmdate( 'Y-m-d H:i:s' );
}

function current_user_can( $capability ) {
	// An editor's view, so the "no data yet" notice shows instead of a blank.
	return true;
}

function wp_json_encode( $value, $flags = 0 ) {
	return json_encode( $value, $flags );
}

/* ---------------------------------------------------------------------- */

$plugin = __DIR__ . '/../wordpress/trinity-rundown';

require_once $plugin . '/includes/storage.php';
require_once $plugin . '/includes/render.php';

/**
 * Turn a payload game into the database row the renderer expects to be handed.
 *
 * The opening line is invented rather than read: it is written by WordPress on
 * the week's first run, never by the pipeline, so a payload alone would never
 * exercise the "(opened ...)" branch. Offsetting it by a point means the
 * preview always shows a moved line, which is the case worth looking at.
 */
function preview_row( array $game, string $updated_at, array $notes, bool $locked ): object {
	$stats = $game;

	$opening = null;
	if ( isset( $stats['odds']['spread'] ) ) {
		$opening = [
			'spread'          => $stats['odds']['spread'] + 1.0,
			'spread_favorite' => $stats['odds']['spread_favorite'] ?? null,
			'total'           => $stats['odds']['total'] ?? null,
		];
	}

	$published = $stats;
	if ( $notes ) {
		$published['notes'] = $notes;
	}

	return (object) [
		'id'             => 0,
		'season'         => $stats['season'] ?? 0,
		'week'           => $stats['week'] ?? 0,
		'game_id'        => $stats['game_id'] ?? '',
		'stats_json'     => wp_json_encode( $stats ),
		'overrides_json' => null,
		'notes_json'     => $notes ? wp_json_encode( $notes ) : null,
		'published_json' => $locked ? wp_json_encode( $published ) : null,
		'opening_line'   => $opening ? wp_json_encode( $opening ) : null,
		'locked'         => $locked ? 1 : 0,
		'sort_order'     => $stats['sort_order'] ?? 0,
		'updated_at'     => $updated_at,
	];
}

/** Sample editorial copy, so the notes sections are visible under --notes. */
function preview_notes(): array {
	return [
		'scouting'   => "Sample scouting copy, here so the editorial sections render at all.\n\nA second paragraph, to check the spacing between them.",
		'td_leans'   => 'A lean, another lean, and a third (sprinkle)',
		'prediction' => 'Home 23, Away 20',
	];
}

/* ---------------------------------------------------------------------- */

$args  = array_slice( $argv, 1 );
$flags = array_values(
	array_filter( $args, static fn( $a ) => str_starts_with( $a, '--' ) )
);
$paths = array_values(
	array_filter( $args, static fn( $a ) => ! str_starts_with( $a, '--' ) )
);

$payload = $paths[0] ?? 'build/2026-week-01.json';
$out     = $paths[1] ?? 'build/preview.html';

$with_notes = in_array( '--notes', $flags, true );
$locked     = in_array( '--locked', $flags, true );
$dark       = in_array( '--dark', $flags, true );

$needle = '';
foreach ( $flags as $flag ) {
	if ( str_starts_with( $flag, '--game=' ) ) {
		$needle = substr( $flag, strlen( '--game=' ) );
	}
}

if ( ! is_readable( $payload ) ) {
	fwrite( STDERR, "No payload at {$payload}. Build one first:\n" );
	fwrite( STDERR, "  python -m pipeline.build_week --season 2026 --week 1 --replay-odds --dry-run\n" );
	exit( 1 );
}

$data  = json_decode( (string) file_get_contents( $payload ), true );
$games = is_array( $data ) && isset( $data['games'] ) ? $data['games'] : [];

if ( ! $games ) {
	fwrite( STDERR, "No games in {$payload}.\n" );
	exit( 1 );
}

if ( '' !== $needle ) {
	$games = array_values(
		array_filter(
			$games,
			static fn( $g ) => str_contains( (string) ( $g['game_id'] ?? '' ), $needle )
		)
	);

	if ( ! $games ) {
		fwrite( STDERR, "No game id contains {$needle}.\n" );
		exit( 1 );
	}
}

$updated_at = isset( $data['generated_at'] )
	? gmdate( 'Y-m-d H:i:s', (int) strtotime( (string) $data['generated_at'] ) )
	: gmdate( 'Y-m-d H:i:s' );

$notes = $with_notes ? preview_notes() : [];
$rows  = array_map(
	static fn( $game ) => preview_row( $game, $updated_at, $notes, $locked ),
	$games
);

$season = (int) ( $data['season'] ?? 0 );
$week   = (int) ( $data['week'] ?? 0 );
$title  = "Rundown preview - {$season} week {$week}";
$assets = '../wordpress/trinity-rundown/assets';
$body   = trun_render_rows( $rows, $season, $week );

$head = '<!doctype html>' . "\n"
	. '<html lang="en">' . "\n"
	. '<head>' . "\n"
	. '<meta charset="utf-8">' . "\n"
	. '<meta name="viewport" content="width=device-width, initial-scale=1">' . "\n"
	. '<title>' . esc_html( $title ) . '</title>' . "\n"
	. '<link rel="stylesheet" href="' . $assets . '/rundown.css">' . "\n"
	. '<style>' . "\n"
	. "\t" . '/* Stand-in for the theme: a content column and readable defaults. */' . "\n"
	. "\t" . 'body { margin: 0; font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;' . "\n"
	. "\t\t" . ( $dark ? 'background: #000; color: #ede8e8;' : 'background: #fff; color: #1a1a1a;' ) . ' }' . "\n"
	. "\t" . '.preview-shell { max-width: 48rem; margin: 0 auto; padding: 2rem 1rem 4rem; }' . "\n"
	. "\t" . '.preview-note { margin: 0 0 2rem; padding: .5rem .75rem; border-inline-start: 3px solid #888;' . "\n"
	. "\t\t" . 'font-size: .8125rem; color: #767676; }' . "\n"
	. "\t" . '@media print { .preview-note { display: none; } }' . "\n"
	. '</style>' . "\n"
	. '</head>' . "\n"
	. '<body>' . "\n"
	. '<div class="preview-shell">' . "\n"
	. '<p class="preview-note">Local preview. Not WordPress: no theme, no admin bar, no block'
	. ' styles, so judge layout and markup here and colour on staging.</p>' . "\n";

$foot = "\n" . '</div>' . "\n"
	. '<script src="' . $assets . '/rundown.js"></script>' . "\n"
	. '</body>' . "\n"
	. '</html>' . "\n";

$dir = dirname( $out );
if ( ! is_dir( $dir ) ) {
	mkdir( $dir, 0777, true );
}

file_put_contents( $out, $head . $body . $foot );

printf(
	"%s -- %d game(s)%s%s\n",
	$out,
	count( $rows ),
	$with_notes ? ', with notes' : '',
	$locked ? ', locked' : ''
);
