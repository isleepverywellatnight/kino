"""Native player view for the KINO window (macOS): mpv renders under the web view.

The controller is called from the local HTTP server's threads. Everything that
touches AppKit or OpenGL runs on the main thread; mpv's client API is
thread-safe. During playback the web view stops drawing its background, so the
page's transparent player overlay shows the video drawn by mpv beneath it.
"""
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import native_player

OPTIONS = {
    "cache": "yes",
    "demuxer-max-bytes": "150MiB",
    "demuxer-max-back-bytes": "50MiB",
    "sub-auto": "fuzzy",
    "sub-margin-y": "90",  # keep subtitles above KINO's control bar
    # KINO hands mpv direct media only: no youtube-dl lookup, no built-in scripts.
    "ytdl": "no",
    "load-scripts": "no",
}
FRENCH = "fr,fre,fra,french"
# « Clarté » levels as (gamma, brightness, contrast): mpv applies them while
# rendering, so hardware decoding is kept (a video filter would copy frames back).
CLARITY_LEVELS = ((0, 0, 0), (12, 3, 2), (22, 6, 3), (32, 9, 4))
# « Voix » boost: dynamic normalisation lifts dialogue and softens peaks.
VOICE_FILTER = "lavfi=[dynaudnorm=f=150:g=15:p=0.9]"
SUBTITLE_MAX_BYTES = 2_000_000


def language_options(pref_lang):
    """mpv track preferences from KINO's « Langue 1-Clic » setting."""
    if pref_lang == "vostfr":
        return {"slang": FRENCH}
    return {"alang": FRENCH}


def mpv_command(cmd, value=None):
    """Map an overlay command to mpv arguments; anything else is refused."""
    if cmd == "toggle":
        return ("cycle", "pause")
    if cmd in ("play", "pause"):
        return ("set", "pause", "yes" if cmd == "pause" else "no")
    if cmd == "seek":
        return ("seek", _number(value, -36000, 36000), "relative")
    if cmd == "goto":
        return ("seek", _number(value, 0, 360000), "absolute")
    if cmd == "volume":
        return ("set", "volume", _number(value, 0, 130))
    if cmd == "mute":
        return ("cycle", "mute")
    if cmd in ("audio", "subtitles"):
        track = "no" if value in (None, "", "no", False) else str(int(_number(value, 1, 999)))
        if cmd == "audio" and track == "no":
            raise ValueError("audio track")
        return ("set", "aid" if cmd == "audio" else "sid", track)
    if cmd == "speed":
        return ("set", "speed", _number(value, 0.25, 4))
    if cmd == "sub_scale":
        return ("set", "sub-scale", _number(value, 0.5, 3))
    if cmd == "voice":
        if value not in ("normal", "boost"):
            raise ValueError("voice mode")
        return ("set", "af", VOICE_FILTER if value == "boost" else "")
    if cmd == "clarity":
        level = _number(value, 0, len(CLARITY_LEVELS) - 1)
        if level != int(level):
            raise ValueError("clarity level")
        gamma, brightness, contrast = CLARITY_LEVELS[int(level)]
        return [("set", "gamma", gamma), ("set", "brightness", brightness), ("set", "contrast", contrast)]
    raise ValueError(f"unknown command {cmd!r}")


def cues_to_srt(cues):
    """SRT text from KINO's subtitle cues [{start, end, text}] (offline subtitles)."""
    def stamp(seconds):
        millis = int(round(max(0.0, float(seconds)) * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    blocks = []
    for cue in cues or []:
        try:
            start, end, text = float(cue["start"]), float(cue["end"]), str(cue["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start:
            blocks.append(f"{len(blocks) + 1}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def extract_frame(source, seconds, path, ffmpeg=None, run=subprocess.run):
    """One frame of a local film at `seconds`, written by FFmpeg (PNG or JPEG from the name)."""
    if ffmpeg is None:
        import player_engine
        ffmpeg = player_engine.find_ffmpeg()
    remote = str(source or "").startswith(("https://", "http://127.0.0.1:"))
    if not ffmpeg or not source or not (remote or Path(source).is_file()):
        raise RuntimeError("Capture impossible.")
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, float(seconds)):.3f}", "-i", str(source),
         "-frames:v", "1", "-y", str(path)], check=True, timeout=30, stdin=subprocess.DEVNULL, capture_output=True)
    if not Path(path).is_file():
        raise RuntimeError("Capture impossible.")
    return Path(path)


def decode_subtitle(raw):
    """Subtitle bytes as text: UTF-8 when valid, else Latin-1 (common for French SRT)."""
    raw = bytes(raw or b"")[:SUBTITLE_MAX_BYTES]
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="ignore")


def _number(value, low, high):
    number = float(value)
    if not low <= number <= high:
        raise ValueError("out of range")
    return round(number, 3)


def _run_on_main(fn, timeout=10):
    from Foundation import NSThread
    if NSThread.isMainThread():
        return fn()
    from PyObjCTools import AppHelper
    done, box = threading.Event(), {}

    def call():
        try:
            box["value"] = fn()
        except BaseException as error:  # re-raised in the caller's thread
            box["error"] = error
        finally:
            done.set()

    AppHelper.callAfter(call)
    if not done.wait(timeout):
        raise TimeoutError("main thread did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _make_view_class():
    import objc
    from AppKit import (NSOpenGLView, NSOpenGLPixelFormat, NSOpenGLPFADoubleBuffer, NSOpenGLPFAAccelerated,
                        NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion3_2Core)

    class KinoMpvView(NSOpenGLView):
        def initWithFrame_(self, frame):
            attrs = [NSOpenGLPFADoubleBuffer, NSOpenGLPFAAccelerated, NSOpenGLPFAOpenGLProfile,
                     NSOpenGLProfileVersion3_2Core, 0]
            pixel_format = NSOpenGLPixelFormat.alloc().initWithAttributes_(attrs)
            self = objc.super(KinoMpvView, self).initWithFrame_pixelFormat_(frame, pixel_format)
            if self is None:
                return None
            self.setWantsBestResolutionOpenGLSurface_(True)
            self.player = None
            return self

        def drawRect_(self, rect):
            player = self.player
            if player is None or player.render_ctx is None:
                return
            context = self.openGLContext()
            context.makeCurrentContext()
            size = self.convertRectToBacking_(self.bounds()).size
            player.render(size.width, size.height)
            context.flushBuffer()

        def pump_(self, timer):
            player = self.player
            if player is not None and player.frame_ready():
                self.setNeedsDisplay_(True)

    return KinoMpvView


class NativePlayerController:
    """One native playback at a time inside the KINO window."""

    def __init__(self, window_and_webview):
        self._window_and_webview = window_and_webview  # () -> (NSWindow, WKWebView)
        self._lock = threading.RLock()
        self._player = None
        self._view = None
        self._timer = None
        self._view_class = None
        self._settings = {}
        self._pending_subtitles = []
        self._subtitle_dir = None
        self._path = None

    # --- called from HTTP threads ---
    def open(self, path, start=0.0, volume=None, pref_lang="vf"):
        with self._lock:
            self._close_locked()
            _run_on_main(lambda: self._open_on_main(path, float(start or 0), volume, pref_lang))
            return self.state()

    def command(self, cmd, value=None):
        args = mpv_command(cmd, value)
        with self._lock:
            if self._player is None:
                raise RuntimeError("Aucune lecture native en cours.")
            for step in args if isinstance(args, list) else [args]:
                self._player.command(*step)
            if cmd in ("voice", "clarity"):
                self._settings[cmd] = value if cmd == "voice" else int(float(value))
            return self.state()

    def screenshot(self, path):
        """Saves the current frame (video only, no subtitles) to `path`, chosen by KINO."""
        with self._lock:
            if self._player is None:
                raise RuntimeError("Aucune lecture native en cours.")
            try:
                self._player.command("screenshot-to-file", str(path), "video")
                return Path(path)
            except Exception:
                # This mpv cannot convert hardware-decoded (VideoToolbox) frames to an
                # image: KINO's FFmpeg reads the same moment from the local file instead.
                source, at = self._path, self._player.get_double("time-pos") or 0.0
        return extract_frame(source, at, path)

    def add_subtitle(self, text, title, lang, select=True):
        """Adds a subtitle file written by KINO (never a URL): once the film is loaded."""
        with self._lock:
            if self._player is None:
                raise RuntimeError("Aucune lecture native en cours.")
            if self._subtitle_dir is None:
                self._subtitle_dir = tempfile.mkdtemp(prefix="kino-native-subs-")
            path = Path(self._subtitle_dir) / f"{len(list(Path(self._subtitle_dir).iterdir())) + 1}.srt"
            path.write_text(text, encoding="utf-8")
            self._pending_subtitles.append((str(path), str(title)[:80], str(lang)[:8], bool(select)))
            self._flush_subtitles_locked()
            return self.state()

    def _flush_subtitles_locked(self):
        # sub-add needs the file loaded; loadfile is asynchronous.
        if not self._pending_subtitles or self._player is None or self._player.get_double("duration") is None:
            return
        pending, self._pending_subtitles = self._pending_subtitles, []
        for path, title, lang, select in pending:
            try:
                self._player.command("sub-add", path, "select" if select else "auto", title, lang)
            except Exception:
                pass

    def state(self):
        with self._lock:
            player = self._player
            if player is None:
                return {"open": False}
            self._flush_subtitles_locked()
            tracks = []
            count = int(player.get_double("track-list/count") or 0)
            for index in range(min(count, 64)):
                kind = player.get_string(f"track-list/{index}/type")
                if kind not in ("audio", "sub"):
                    continue
                tracks.append({
                    "type": kind, "id": player.get_string(f"track-list/{index}/id"),
                    "lang": player.get_string(f"track-list/{index}/lang") or "",
                    "title": player.get_string(f"track-list/{index}/title") or "",
                    "codec": player.get_string(f"track-list/{index}/codec") or "",
                    "channels": player.get_string(f"track-list/{index}/demux-channel-count") or "",
                    "selected": player.get_flag(f"track-list/{index}/selected") or False,
                    "external": player.get_flag(f"track-list/{index}/external") or False,
                })
            return {
                "open": True,
                "time": player.get_double("time-pos") or 0.0,
                "duration": player.get_double("duration") or 0.0,
                "paused": bool(player.get_flag("pause")),
                "ended": bool(player.get_flag("eof-reached")),
                # A source that could not be opened leaves mpv idle with no duration.
                "idle": bool(player.get_flag("idle-active")),
                "buffering": bool(player.get_flag("paused-for-cache")) or player.get_double("duration") is None,
                "volume": player.get_double("volume"),
                "muted": bool(player.get_flag("mute")),
                "width": player.get_string("video-params/w"),
                "height": player.get_string("video-params/h"),
                "hwdec": player.get_string("hwdec-current"),
                "speed": player.get_double("speed") or 1.0,
                "sub_scale": player.get_double("sub-scale") or 1.0,
                "voice": self._settings.get("voice", "normal"),
                "clarity": self._settings.get("clarity", 0),
                "tracks": tracks,
            }

    def close(self):
        with self._lock:
            self._close_locked()
        return {"open": False}

    # --- main thread ---
    def _close_locked(self):
        if self._player is not None or self._view is not None:
            _run_on_main(self._close_on_main)
        self._settings = {}
        self._pending_subtitles = []
        if self._subtitle_dir is not None:
            shutil.rmtree(self._subtitle_dir, ignore_errors=True)
            self._subtitle_dir = None

    def _open_on_main(self, path, start, volume, pref_lang):
        from AppKit import NSWindowBelow, NSViewWidthSizable, NSViewHeightSizable
        from Foundation import NSTimer, NSRunLoop, NSRunLoopCommonModes
        window, webview = self._window_and_webview()
        if window is None or webview is None:
            raise RuntimeError("Fenêtre KINO introuvable.")
        if self._view_class is None:
            self._view_class = _make_view_class()
        options = dict(OPTIONS, **language_options(pref_lang))
        if volume is not None:
            options["volume"] = str(_number(volume, 0, 130))
        if start > 0:
            options["start"] = str(round(start, 3))
        player = native_player.Mpv(**{key.replace("-", "_"): value for key, value in options.items()})
        try:
            view = self._view_class.alloc().initWithFrame_(webview.frame())
            view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            view.setWantsLayer_(True)
            webview.superview().addSubview_positioned_relativeTo_(view, NSWindowBelow, webview)
            # The render context must exist before mpv opens its video output.
            view.openGLContext().makeCurrentContext()
            player.create_render_context()
            view.player = player
            player.command("loadfile", path)
        except BaseException:
            player.close()
            raise
        webview.setValue_forKey_(False, "drawsBackground")
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(1 / 120, view, b"pump:", None, True)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._player, self._view, self._timer = player, view, timer
        self._path = path

    def _close_on_main(self):
        player, view, timer = self._player, self._view, self._timer
        self._player = self._view = self._timer = self._path = None
        if timer is not None:
            timer.invalidate()
        if view is not None:
            view.player = None
            view.openGLContext().makeCurrentContext()
        if player is not None:
            player.close()
        if view is not None:
            view.removeFromSuperview()
        try:
            _, webview = self._window_and_webview()
            if webview is not None:
                webview.setValue_forKey_(True, "drawsBackground")
                webview.setNeedsDisplay_(True)
        except Exception:
            pass
