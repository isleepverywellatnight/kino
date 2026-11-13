"""macOS « À l'écoute » and the Mac's playback keys for KINO.

What either player is showing (the state both send each second to
remote_controller) appears in Control Centre and on the lock screen: title,
episode, poster, time. The play/pause key of the keyboard, AirPods and the
Control Centre buttons reach the player through the command queue the phone
remote already uses, so both players obey them without new code of their own.

Apple's MediaPlayer framework is loaded from the system (no extra package).
Only inside the KINO.app bundle: elsewhere (tests, the development Python)
`NowPlaying.create()` returns None.
"""
import threading
import time
import urllib.request

BUNDLE_ID = "com.kino.desktop"
FRAMEWORK = "/System/Library/Frameworks/MediaPlayer.framework"
# A player that has not reported for this long is closed (it reports every second).
STALE_AFTER = 4.0
# Next / previous on a film: a jump in the film, as the remote's buttons.
SKIP_SECONDS = 10
# Elapsed time drifts by itself (rate 1): resend it only when it is this far off.
DRIFT = 2.0
POSTER_MAX_BYTES = 3 * 1024 * 1024

# MPRemoteCommandHandlerStatus and MPNowPlayingPlaybackState.
HANDLED, NOTHING_PLAYING = 0, 110
PLAYING, PAUSED, STOPPED = 1, 2, 3


def now_playing_fields(state, now):
    """What « À l'écoute » shows for this player state, or None when nothing plays."""
    if not isinstance(state, dict) or not state.get("playing"):
        return None
    try:
        if now - float(state.get("updated_at") or 0) > STALE_AFTER:
            return None
        duration = max(0.0, float(state.get("duration") or 0))
        elapsed = max(0.0, float(state.get("current_time") or 0))
    except (TypeError, ValueError):
        return None
    if duration:
        elapsed = min(elapsed, duration)
    paused = bool(state.get("paused"))
    return {
        "title": str(state.get("title") or "").strip() or "KINO",
        "artist": str(state.get("sub_title") or "").strip() or "KINO",
        "duration": duration,
        "elapsed": elapsed,
        "rate": 0.0 if paused else 1.0,
        "poster": str(state.get("poster") or "") if str(state.get("poster") or "").startswith("https://") else "",
    }


def needs_update(previous, fields, now, sent_at):
    """Whether the system must be told again: something changed, or the time jumped."""
    if previous is None or fields is None:
        return previous != fields
    if any(previous[key] != fields[key] for key in ("title", "artist", "duration", "rate", "poster")):
        return True
    predicted = previous["elapsed"] + max(0.0, now - sent_at) * previous["rate"]
    return abs(predicted - fields["elapsed"]) > DRIFT


def command_for(kind, state, now, position=None):
    """The remote command ({action, value}) for a key or a Control Centre button, or None.

    Play/pause is sent as an explicit play or pause, from the state before the
    press: if the web view's own media handling also toggles the video, the end
    result is still the one press asked for."""
    if now_playing_fields(state, now) is None:
        return None
    paused = bool(state.get("paused"))
    series = state.get("media_type") == "series" or bool(state.get("sub_title"))
    if kind == "toggle":
        return {"action": "play" if paused else "pause", "value": None}
    if kind in ("play", "pause"):
        return {"action": kind, "value": None}
    if kind == "next":
        return {"action": "next_ep", "value": None} if series else {"action": "seek_rel", "value": SKIP_SECONDS}
    if kind == "previous":
        return {"action": "prev_ep", "value": None} if series else {"action": "seek_rel", "value": -SKIP_SECONDS}
    if kind == "position" and position is not None:
        try:
            return {"action": "seek", "value": max(0.0, float(position))}
        except (TypeError, ValueError):
            return None
    return None


def running_bundle_id():
    try:
        from Foundation import NSBundle
        return str(NSBundle.mainBundle().bundleIdentifier() or "")
    except Exception:
        return ""


def load_framework():
    """MediaPlayer's classes and keys, with the artwork block described for PyObjC."""
    import objc
    names = {}
    bundle = objc.loadBundle("MediaPlayer", names, bundle_path=FRAMEWORK)
    keys = ("MPMediaItemPropertyTitle", "MPMediaItemPropertyArtist", "MPMediaItemPropertyPlaybackDuration",
            "MPNowPlayingInfoPropertyElapsedPlaybackTime", "MPNowPlayingInfoPropertyPlaybackRate", "MPMediaItemPropertyArtwork")
    objc.loadBundleVariables(bundle, names, [(key, b"@") for key in keys])
    # The artwork's request handler is a block: argument 3 counting self and _cmd.
    objc.registerMetaDataForSelector(b"MPMediaItemArtwork", b"initWithBoundsSize:requestHandler:", {
        "arguments": {3: {"callable": {"retval": {"type": b"@"}, "arguments": {0: {"type": b"^v"}, 1: {"type": b"{CGSize=dd}"}}}}}})
    return names


_TARGET_CLASS = None


def _target_class():
    """An Objective-C target for one command: its handler returns a status."""
    global _TARGET_CLASS
    if _TARGET_CLASS is not None:
        return _TARGET_CLASS
    import objc
    from Foundation import NSObject

    class KinoRemoteCommandTarget(NSObject):
        @objc.typedSelector(b"q@:@")
        def handle_(self, event):
            try:
                position = event.positionTime() if self.kind == "position" else None
            except Exception:
                position = None
            try:
                return self.owner.handle(self.kind, position)
            except Exception:
                return NOTHING_PLAYING

    _TARGET_CLASS = KinoRemoteCommandTarget
    return _TARGET_CLASS


class NowPlaying:
    """Keeps « À l'écoute » in step with the player and sends the keys to it."""

    def __init__(self, framework, get_state, push_command, call_on_main, clock=time.time):
        self._mp = framework
        self._get_state = get_state
        self._push = push_command
        self._main = call_on_main
        self._clock = clock
        self._targets = []
        self._shown = None
        self._sent_at = 0.0
        self._artwork = {}  # poster URL -> MPMediaItemArtwork (or None while loading / failed)
        self._stop = threading.Event()

    @classmethod
    def create(cls, get_state, push_command, call_on_main, bundle_id=None, framework=None):
        if (bundle_id if bundle_id is not None else running_bundle_id()) != BUNDLE_ID:
            return None
        try:
            return cls(framework or load_framework(), get_state, push_command, call_on_main)
        except Exception:
            return None

    # --- commands -------------------------------------------------------
    def handle(self, kind, position=None):
        command = command_for(kind, self._get_state(), self._clock(), position)
        if command is None:
            return NOTHING_PLAYING
        self._push(command["action"], command["value"])
        return HANDLED

    def _register(self):
        """On the main thread: one target per command this player understands."""
        center = self._mp["MPRemoteCommandCenter"].sharedCommandCenter()
        commands = {
            "toggle": center.togglePlayPauseCommand(), "play": center.playCommand(), "pause": center.pauseCommand(),
            "next": center.nextTrackCommand(), "previous": center.previousTrackCommand(),
            "position": center.changePlaybackPositionCommand(),
        }
        for kind, command in commands.items():
            target = _target_class().alloc().init()
            target.kind, target.owner = kind, self
            command.setEnabled_(True)
            command.addTarget_action_(target, "handle:")
            self._targets.append((command, target))  # kept alive: the centre holds them weakly

    # --- what is playing ------------------------------------------------
    def tick(self):
        """Once a second: tell the system when what plays has changed."""
        now = self._clock()
        fields = now_playing_fields(self._get_state(), now)
        if fields and fields["poster"] and fields["poster"] not in self._artwork:
            if len(self._artwork) >= 20:
                self._artwork.clear()
            self._artwork[fields["poster"]] = None
            threading.Thread(target=self._load_poster, args=(fields["poster"],), daemon=True).start()
        if not needs_update(self._shown, fields, now, self._sent_at):
            return False
        self._shown, self._sent_at = fields, now
        self._main(lambda: self._apply(fields))
        return True

    def _info(self, fields):
        mp = self._mp
        info = {
            mp["MPMediaItemPropertyTitle"]: fields["title"],
            mp["MPMediaItemPropertyArtist"]: fields["artist"],
            mp["MPNowPlayingInfoPropertyElapsedPlaybackTime"]: fields["elapsed"],
            mp["MPNowPlayingInfoPropertyPlaybackRate"]: fields["rate"],
        }
        if fields["duration"]:
            info[mp["MPMediaItemPropertyPlaybackDuration"]] = fields["duration"]
        artwork = self._artwork.get(fields["poster"])
        if artwork is not None:
            info[mp["MPMediaItemPropertyArtwork"]] = artwork
        return info

    def _apply(self, fields):
        center = self._mp["MPNowPlayingInfoCenter"].defaultCenter()
        if fields is None:
            center.setNowPlayingInfo_(None)
            center.setPlaybackState_(STOPPED)
            return
        center.setNowPlayingInfo_(self._info(fields))
        center.setPlaybackState_(PLAYING if fields["rate"] else PAUSED)

    def _load_poster(self, url):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "KINO"})
            with urllib.request.urlopen(request, timeout=6) as response:
                data = response.read(POSTER_MAX_BYTES + 1)
            if len(data) > POSTER_MAX_BYTES:
                return
            from AppKit import NSImage
            from Foundation import NSData
            image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            if image is None:
                return
            size = image.size()
            artwork = self._mp["MPMediaItemArtwork"].alloc().initWithBoundsSize_requestHandler_(size, lambda _size: image)
            self._artwork[url] = artwork
            self._shown = None  # shown again at the next tick, with the poster
        except Exception:
            pass

    # --- life -----------------------------------------------------------
    def start(self):
        self._main(self._register)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
