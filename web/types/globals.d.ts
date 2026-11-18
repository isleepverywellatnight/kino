// Names KINO's pages share between scripts, for the TypeScript check
// (`npx typescript tsc -p jsconfig.json`). Nothing here is served or run.

// Scripts that also hand themselves to the Node tests (`module.exports`) are
// modules to TypeScript: their page-wide name is declared here, with the type
// of what they export.
declare const Spoilers: typeof import('../js/spoilers.js');
declare const SeriesPlayback: typeof import('../js/series-playback.js');
declare const AnimeJourneyView: typeof import('../js/anime-journey.js');

// Set by web/js/i18n.js on window.
declare const KINO_I18N: {
  lang: string;
  load(table: Record<string, string>): void;
  t(text: string): string;
  months(style: 'long' | 'short' | 'narrow'): string[];
  choose(next: string): Promise<void>;
};

// Vendored libraries (web/js/vendor), typed only as far as KINO uses them.
declare const QRCode: any;
declare const Peer: any;

interface Window {
  /** The desktop app's bridge (crates/kino-desktop/src/bridge.rs). */
  pywebview?: { api: Record<string, (...args: unknown[]) => Promise<any>> };
  KINO_I18N: typeof KINO_I18N;
  KINO_LANG: string;
  KINO_LOCALE: string;
  KINO_LANGUAGES: Record<string, string>;
  webkitAudioContext?: typeof AudioContext;
}

// WebKit's prefixed fullscreen API (WKWebView on macOS).
interface Document {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
  mozFullScreenElement?: Element | null;
  msFullscreenElement?: Element | null;
}
interface HTMLElement {
  webkitRequestFullscreen?: () => Promise<void> | void;
}
