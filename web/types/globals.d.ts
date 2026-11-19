// Names KINO's pages share between scripts, for the TypeScript check
// (`npx typescript tsc -p jsconfig.json`). Nothing here is served or run.

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
  /** The desktop app's bridge (web/types/bridge.d.ts). */
  pywebview?: { api: KinoBridge; platform: string };
  KINO_I18N: typeof KINO_I18N;
  KINO_LANG: string;
  KINO_LOCALE: string;
  KINO_LANGUAGES: Record<string, string>;
  webkitAudioContext?: typeof AudioContext;
}

// WebKit's prefixed fullscreen API (WKWebView on macOS).
interface Window {
  /** WKWebView's message handlers (the macOS window). */
  webkit?: { messageHandlers?: Record<string, { postMessage(message: string): void }> };
  /** WebView2 (the Windows window). */
  chrome?: { webview?: unknown };
  /** The interface language a launcher may set before i18n.js runs. */
  KINO_UI_LANGUAGE?: string;
}

// KINO's own player keeps a few facts on its <video> (web/js/app-player.js),
// and Safari's picture-in-picture API.
interface HTMLVideoElement {
  /** The user muted the sound themselves (not KINO's start-up mute). */
  _userMuted?: boolean;
  /** The listeners of the stream now playing, removed before the next. */
  _onPlayingHandler?: EventListener;
  _onErrorHandler?: EventListener;
  webkitSupportsPresentationMode?(mode: string): boolean;
  webkitSetPresentationMode?(mode: string): void;
  webkitPresentationMode?: string;
  /** Safari's name for preservesPitch. */
  webkitPreservesPitch?: boolean;
}

interface Document {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
  mozFullScreenElement?: Element | null;
  msFullscreenElement?: Element | null;
}
interface HTMLElement {
  webkitRequestFullscreen?: () => Promise<void> | void;
}
