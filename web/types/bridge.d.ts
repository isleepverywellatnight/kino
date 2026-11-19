// The desktop app's bridge, window.pywebview.api: one line per name the page
// may call (BRIDGE in crates/kino-desktop/src/main.rs, decoded by
// BridgeCall::parse in crates/kino-desktop/src/bridge.rs). Every call returns a
// promise; a refused call rejects with the app's message.
// tests/test_bridge_types.js fails when this list and the app's differ.
// Nothing here is served or run.

/** What get_state(), set_bounds() and resize() answer (logical pixels). */
interface KinoWindowState {
  maximized: boolean;
  fullscreen: boolean;
  width: number;
  height: number;
  x: number;
  y: number;
  /** Rust's std::env::consts::OS: 'macos', 'windows', 'linux'… */
  platform: string;
}

/** An edge or corner of the window, for start_resize(). */
type KinoResizeEdge = 'left' | 'right' | 'top' | 'bottom'
  | 'topleft' | 'top-left' | 'topright' | 'top-right' | 'bottomleft' | 'bottom-left' | 'bottomright' | 'bottom-right';

/** A file dialog the user closed without choosing. */
interface KinoCancelled { cancelled: true }

/** The updater's answers (crates/kino/src/update.rs): its status, as JSON. */
type KinoUpdateAnswer = Record<string, any>;

interface KinoBridge {
  /** Each answers the state that follows: true when it happened. */
  minimize(): Promise<boolean>;
  toggle_maximize(): Promise<boolean>;
  /** False when the mouse button is no longer down (Windows). */
  start_window_drag(): Promise<boolean>;
  start_resize(edge: KinoResizeEdge): Promise<boolean>;
  is_fullscreen(): Promise<boolean>;
  /** Whether the window is in full screen afterwards. */
  toggle_fullscreen(): Promise<boolean>;
  enter_fullscreen(): Promise<boolean>;
  exit_fullscreen(): Promise<boolean>;
  close(): Promise<boolean>;
  /** Position and inner size; a size below 640 × 420 is raised to it. */
  set_bounds(x?: number, y?: number, width?: number, height?: number): Promise<KinoWindowState>;
  resize(width: number, height: number): Promise<KinoWindowState>;
  get_state(): Promise<KinoWindowState>;
  /** The series of the chosen folder (as GET /api/manga/local). */
  choose_manga_folder(): Promise<KinoCancelled | Record<string, any>>;
  /** Only finite numbers, booleans and null of the known fields are written. False when not saved. */
  export_playback_diagnostic(report: Record<string, unknown>): Promise<boolean>;
  update_status(): Promise<KinoUpdateAnswer>;
  set_update_auto_check(enabled: boolean): Promise<KinoUpdateAnswer>;
  check_for_updates(): Promise<KinoUpdateAnswer>;
  open_release_page(): Promise<{ opened: boolean }>;
  choose_update_package(): Promise<KinoCancelled | KinoUpdateAnswer>;
  /** { deferred: true, message } while something plays. */
  install_update(): Promise<KinoUpdateAnswer>;
  rollback_update(): Promise<KinoUpdateAnswer>;
  /** Rust's std::env::consts::OS. */
  platform(): Promise<string>;
}
