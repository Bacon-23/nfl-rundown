<?php
/**
 * Plugin Name:       Trinity Rundown
 * Plugin URI:        https://github.com/Bacon-23/nfl-rundown
 * Description:       Weekly NFL matchup dashboards. Stats arrive from the pipeline over REST; editorial commentary is written in wp-admin. The two never overwrite each other.
 * Version:           0.2.0
 * Requires at least: 6.4
 * Requires PHP:      8.1
 * Author:            Trinity Analytics
 * License:           GPL-2.0-or-later
 * Text Domain:       trinity-rundown
 */

defined( 'ABSPATH' ) || exit;

define( 'TRUN_VERSION', '0.2.0' );
define( 'TRUN_FILE', __FILE__ );
define( 'TRUN_DIR', plugin_dir_path( __FILE__ ) );
define( 'TRUN_URL', plugin_dir_url( __FILE__ ) );

require_once TRUN_DIR . 'includes/storage.php';
require_once TRUN_DIR . 'includes/rest-ingest.php';
require_once TRUN_DIR . 'includes/render.php';
require_once TRUN_DIR . 'includes/shortcode.php';

// The writer's screen. Admin-only, and admin-post.php counts as admin,
// so the save/publish handlers registered inside it are still reachable.
if ( is_admin() ) {
	require_once TRUN_DIR . 'includes/admin-week.php';
}

if ( defined( 'WP_CLI' ) && WP_CLI ) {
	require_once TRUN_DIR . 'includes/cli.php';
}

register_activation_hook( __FILE__, [ 'TRUN_Storage', 'install' ] );

/**
 * Run the schema installer when the stored version lags the plugin version.
 *
 * GitHub Deployments overwrites plugin files without deactivating the plugin,
 * so the activation hook does not fire on deploy. This is the upgrade path.
 */
add_action(
	'plugins_loaded',
	function () {
		if ( get_option( 'trun_db_version' ) !== TRUN_VERSION ) {
			TRUN_Storage::install();
		}
	}
);
