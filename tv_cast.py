"""« Lire sur la TV »: a film or an episode plays on a TV of the
local network (DLNA / UPnP AVTransport, as Samsung TVs understand it).

The Mac keeps all the work: it finds the TV (SSDP), resolves the Debrid source
as for any play, and relays the stream over plain HTTP on the local network
(the TV's player refuses https links: tried on a Samsung Q60BA on 2026-11-12).
While the TV plays, this module reads its position every two seconds: KINO's
history, « À l'écoute », the phone remote and the Mac's playback keys follow it
through remote_controller, as they do for the players of the window.

Standard library only; the network calls go through small functions that the
tests replace.
"""
import html
import ipaddress
import re
import secrets
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SSDP_ADDR = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
POLL_EVERY = 2.0
SAVE_EVERY = 15.0
# A TV that has not started after this long refused the source.
START_TIMEOUT = 25.0
# Series on the TV: the next episode is prepared near the end, then
# starts this many seconds after the end (« Arrêter » cancels it).
NEXT_DELAY = 10.0
PREFETCH_AT = 0.85
RELAY_CHUNK = 256 * 1024
# DLNA: the stream can be sought by byte ranges (OP=01), streamed (FLAGS). Without
# it a Samsung TV refuses to seek (error 701, tried on 2026-11-12).
DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

MIME_BY_EXTENSION = {
    ".mkv": "video/x-mkv", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".webm": "video/webm", ".ts": "video/vnd.dlna.mpeg-tts", ".m2ts": "video/vnd.dlna.mpeg-tts",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
}


def mime_for(filename, url=""):
    for name in (filename, urllib.parse.urlsplit(url or "").path):
        match = re.search(r"\.[a-z0-9]{2,4}$", str(name or "").lower())
        if match and match.group(0) in MIME_BY_EXTENSION:
            return MIME_BY_EXTENSION[match.group(0)]
    return "video/x-mkv"


def hms(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def seconds_of(text):
    """« 1:02:03 » or « 0:00:04.372 » → seconds; anything else → 0."""
    match = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*", str(text or ""))
    if not match:
        return 0.0
    hours, minutes, secs = (int(part) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + secs)


# --- Finding the TVs -----------------------------------------------------
def local_ip():
    """This Mac's address on the local network (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def ssdp_search(timeout=3.0, interface_ip=None):
    """The description addresses (LOCATION) of the media renderers that answer."""
    interface_ip = interface_ip or local_ip()
    message = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
               f"MX: 2\r\nST: {RENDERER}\r\n\r\n").encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    locations = []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
            sock.bind((interface_ip, 0))
        except OSError:
            pass
        sock.settimeout(0.4)
        for _ in range(2):
            sock.sendto(message, SSDP_ADDR)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = sock.recvfrom(65507)
            except socket.timeout:
                continue
            match = re.search(r"(?im)^location:\s*(\S+)", data.decode("utf-8", errors="ignore"))
            if match and match.group(1) not in locations:
                locations.append(match.group(1))
    finally:
        sock.close()
    return locations


def http_get(url, timeout=5):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "KINO"}), timeout=timeout) as response:
        return response.read(512 * 1024).decode("utf-8", errors="ignore")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_description(location, xml_text):
    """A renderer from its UPnP description, or None if it cannot play (no AVTransport)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    device = next((el for el in root.iter() if _local(el.tag) == "device"), None)
    if device is None:
        return None

    def text(parent, name):
        el = next((child for child in parent if _local(child.tag) == name), None)
        return (el.text or "").strip() if el is not None else ""

    control = ""
    for service in (el for el in root.iter() if _local(el.tag) == "service"):
        if text(service, "serviceType").startswith("urn:schemas-upnp-org:service:AVTransport:"):
            control = urllib.parse.urljoin(location, text(service, "controlURL"))
    if not control:
        return None
    host = urllib.parse.urlsplit(location).hostname or ""
    return {
        "location": location, "control_url": control, "host": host,
        "name": text(device, "friendlyName") or host, "maker": text(device, "manufacturer"),
        "model": text(device, "modelName"),
    }


def discover(search=ssdp_search, fetch=http_get):
    """The TVs (renderers that can play) of the local network, Samsung ones first."""
    found = []
    for location in search():
        if not is_local_url(location):
            continue
        try:
            renderer = parse_description(location, fetch(location))
        except Exception:
            renderer = None
        if renderer and all(other["control_url"] != renderer["control_url"] for other in found):
            found.append(renderer)
    return sorted(found, key=lambda r: (0 if "samsung" in r["maker"].lower() else 1, r["name"].lower()))


def is_local_url(url):
    """Only addresses of the local network: a description could point anywhere."""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private and not address.is_loopback


# --- Talking to the TV -----------------------------------------------------
def soap_call(control_url, action, args="", timeout=8):
    """One AVTransport action; returns the reply's text. A UPnP error raises."""
    body = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{AVTRANSPORT}"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>')
    request = urllib.request.Request(control_url, data=body.encode("utf-8"), headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{AVTRANSPORT}#{action}"', "User-Agent": "KINO"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as error:
        reply = error.read().decode("utf-8", errors="ignore")
        code = re.search(r"<errorCode>(\d+)</errorCode>", reply)
        raise TvError(int(code.group(1)) if code else error.code) from None


class TvError(Exception):
    MESSAGES = {
        701: "La TV n'a pas accepté la commande.",
        714: "La TV ne lit pas ce type de fichier.",
        716: "La TV ne trouve pas la vidéo.",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES.get(code, f"La TV a répondu par une erreur ({code})."))


def didl(title, url, mime):
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="0" parentID="-1" restricted="1">'
            f'<dc:title>{html.escape(title)}</dc:title><upnp:class>object.item.videoItem</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:{DLNA_FEATURES}">{html.escape(url)}</res></item></DIDL-Lite>')


def _tag(reply, name):
    match = re.search(rf"<{name}>(.*?)</{name}>", reply or "", re.S)
    return html.unescape(match.group(1).strip()) if match else ""


class Renderer:
    def __init__(self, info, call=soap_call):
        self.info = info
        self._call = call

    def _do(self, action, args=""):
        return self._call(self.info["control_url"], action, args)

    def load(self, url, title, mime):
        meta = didl(title, url, mime)
        self._do("SetAVTransportURI", f"<CurrentURI>{html.escape(url)}</CurrentURI><CurrentURIMetaData>{html.escape(meta)}</CurrentURIMetaData>")

    def play(self):
        self._do("Play", "<Speed>1</Speed>")

    def pause(self):
        self._do("Pause")

    def stop(self):
        self._do("Stop")

    def seek(self, seconds):
        self._do("Seek", f"<Unit>REL_TIME</Unit><Target>{hms(seconds)}</Target>")

    def status(self):
        """(state, position, duration): PLAYING, PAUSED_PLAYBACK, STOPPED, TRANSITIONING…"""
        state = _tag(self._do("GetTransportInfo"), "CurrentTransportState")
        position = self._do("GetPositionInfo")
        return state, seconds_of(_tag(position, "RelTime")), seconds_of(_tag(position, "TrackDuration"))


# --- One play on the TV -----------------------------------------------------
class TvCast:
    """The play under way on a TV (one at a time), its relay and its follow-up."""

    def __init__(self, port_getter, record_history, player_state, pop_commands, presence=None,
                 renderer_for=Renderer, opener=urllib.request.urlopen, clock=time.time, ip_getter=local_ip,
                 next_of=None, resolve=None, prefetch=None):
        self._port = port_getter
        self._record = record_history
        self._state = player_state
        self._pop = pop_commands
        self._presence = presence
        self._renderer_for = renderer_for
        self._open = opener
        self._clock = clock
        self._ip = ip_getter
        # Series: next_of(media) -> the next episode's media or None (series over);
        # resolve(media) -> (stream_url, filename); prefetch(media) prepares that link near the end.
        self._next_of = next_of
        self._resolve = resolve
        self._prefetch = prefetch
        self._lock = threading.RLock()
        self.session = None
        self._thread = None

    # The relay: the TV asks KINO, KINO asks Debrid (https), Range passed through.
    def relay_allowed(self, client_ip, path):
        with self._lock:
            session = self.session
        if not session or not path.startswith("/tv/stream/"):
            return False
        token = path[len("/tv/stream/"):].split("/", 1)[0]
        return secrets.compare_digest(token, session["token"]) and client_ip in (session["device"]["host"], "127.0.0.1")

    def relay(self, handler, head=False):
        with self._lock:
            session = self.session
        if not session:
            handler.send_error(404)
            return
        headers = {"User-Agent": "KINO"}
        if handler.headers.get("Range"):
            headers["Range"] = handler.headers["Range"]
        request = urllib.request.Request(session["source"], headers=headers, method="HEAD" if head else "GET")
        try:
            upstream = self._open(request, timeout=20)
        except urllib.error.HTTPError as error:
            upstream = error
        except Exception:
            handler.send_error(502)
            return
        try:
            status = getattr(upstream, "status", None) or getattr(upstream, "code", 502)
            handler.send_response(status)
            handler.send_header("Content-Type", session["mime"])
            for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if upstream.headers.get(name):
                    handler.send_header(name, upstream.headers[name])
            handler.send_header("transferMode.dlna.org", "Streaming")
            handler.send_header("contentFeatures.dlna.org", DLNA_FEATURES)
            handler.end_headers()
            if head:
                return
            while True:
                chunk = upstream.read(RELAY_CHUNK)
                if not chunk:
                    break
                handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def start(self, device, source_url, title, media=None, filename="", resume_sec=0):
        """Sends the source to the TV and starts following it. Raises TvError or ValueError."""
        if not str(source_url or "").startswith(("https://", "http://")):
            raise ValueError("Aucune source à envoyer à la TV.")
        if not device or not device.get("control_url") or not is_local_url(device.get("location", "")):
            raise ValueError("Aucune TV choisie.")
        self.stop(quiet=True)
        token = secrets.token_urlsafe(24)
        mime = mime_for(filename, source_url)
        session = {
            "device": device, "source": source_url, "token": token, "mime": mime,
            "title": str(title or "KINO")[:200], "media": dict(media or {}), "filename": str(filename or ""),
            "resume": max(0.0, float(resume_sec or 0)), "started_at": self._clock(), "state": "TRANSITIONING",
            "position": 0.0, "duration": 0.0, "played": False, "saved_at": 0.0, "error": "", "ended": False,
            "next": None, "prefetched": False,
        }
        safe_name = urllib.parse.quote(re.sub(r"[^\w.\- ]", "", session["filename"] or "video")[:80] or "video")
        relay_url = f"http://{self._ip()}:{self._port()}/tv/stream/{token}/{safe_name}"
        renderer = self._renderer_for(device)
        with self._lock:
            self.session = session
            session["renderer"] = renderer
        try:
            renderer.load(relay_url, session["title"], mime)
            renderer.play()
        except Exception:
            with self._lock:
                self.session = None
            raise
        self._thread = threading.Thread(target=self._follow, args=(session,), daemon=True)
        self._thread.start()
        return self.status()

    def control(self, action, value=None):
        with self._lock:
            session = self.session
        if not session:
            raise ValueError("Rien n'est lu sur la TV.")
        if session["next"]:
            # Between two episodes: start the next one now, or stop there.
            if action in ("play", "toggle_play", "next_now") and not session["next"].get("finding"):
                session["next"]["at"] = self._clock()
            elif action in ("stop", "cancel_next"):
                self.stop()
            return self.status()
        renderer = session["renderer"]
        if action == "play":
            renderer.play()
        elif action == "pause":
            renderer.pause()
        elif action == "toggle_play":
            (renderer.play if session["state"] == "PAUSED_PLAYBACK" else renderer.pause)()
        elif action in ("seek_rel", "seek"):
            target = (session["position"] + float(value or 0)) if action == "seek_rel" else float(value or 0)
            if session["duration"]:
                target = min(target, session["duration"] - 1)
            renderer.seek(max(0.0, target))
            session["position"] = max(0.0, target)
        elif action == "stop":
            self.stop()
        return self.status()

    def stop(self, quiet=False):
        with self._lock:
            session, self.session = self.session, None
        if not session:
            return
        self._save(session, force=True)
        session["ended"] = True
        try:
            session["renderer"].stop()
        except Exception:
            if not quiet:
                pass
        self._state({"playing": False, "paused": True})
        if self._presence:
            try:
                self._presence(None)
            except Exception:
                pass

    def status(self):
        with self._lock:
            session = self.session
        if not session:
            return {"active": False}
        status = {
            "active": True, "device": session["device"]["name"], "title": session["title"],
            "state": session["state"], "paused": session["state"] == "PAUSED_PLAYBACK",
            "position": session["position"], "duration": session["duration"], "error": session["error"],
            "starting": not session["played"],
        }
        pending = session["next"]
        if pending:
            status["next"] = {"code": self._episode_code(pending["media"]),
                              "in": max(0, int(round(pending["at"] - self._clock()))),
                              "finding": pending.get("finding", False)}
        return status

    # --- following the play ----------------------------------------------
    def _follow(self, session):
        while not session["ended"]:
            time.sleep(POLL_EVERY)
            if session["ended"]:
                break
            try:
                self.tick(session)
            except Exception:
                pass

    @staticmethod
    def _episode_code(media):
        season, episode = media.get("season"), media.get("episode")
        return f"S{int(season):02d}E{int(episode):02d}" if season and episode else ""

    def tick(self, session):
        """One look at the TV: its state for KINO, the progress saved, the commands applied."""
        if session["next"]:
            self._wait_next(session)
            return
        renderer = session["renderer"]
        try:
            state, position, duration = renderer.status()
        except Exception:
            return
        session["state"], session["duration"] = state, duration or session["duration"]
        if state in ("PLAYING", "PAUSED_PLAYBACK"):
            if not session["played"]:
                session["played"] = True
                if session["resume"] > 30 and position < session["resume"] - 10:
                    try:
                        renderer.seek(session["resume"])
                        position = session["resume"]
                    except Exception:
                        pass
            session["position"] = position
            media = session["media"]
            if (self._prefetch and self._next_of and not session["prefetched"] and media.get("season")
                    and session["duration"] and position >= PREFETCH_AT * session["duration"]):
                session["prefetched"] = True
                threading.Thread(target=self._prefetch_next, args=(dict(media),), daemon=True).start()
        elif state in ("STOPPED", "NO_MEDIA_PRESENT"):
            if session["played"]:
                # Watched to the end, or stopped from the TV's own remote.
                near_end = session["duration"] and session["position"] >= session["duration"] - 90
                if near_end:
                    session["position"] = session["duration"]
                    upcoming = self._upcoming(session)
                    if upcoming:
                        self._save(session, force=True)
                        session["next"] = {"at": self._clock() + NEXT_DELAY, "media": upcoming}
                        self._share(session)  # paused: the play key starts the next one now
                        return
                self._end(session)
                return
            if self._clock() - session["started_at"] > START_TIMEOUT:
                session["error"] = "La TV n'a pas pu lire cette source."
                self._end(session, keep_error=True)
                return
        self._share(session)
        self._save(session)
        for command in self._pop() or []:
            try:
                self.control(command.get("action"), command.get("value"))
            except Exception:
                pass

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return None

    def _upcoming(self, session):
        """The episode after this one, or None: a film, the series over, or no way to find it."""
        media = session["media"]
        if not (self._next_of and self._resolve and media.get("season") and media.get("episode")):
            return None
        upcoming = self._safe(self._next_of, dict(media))
        if not upcoming or not upcoming.get("season") or not upcoming.get("episode"):
            return None
        return {**media, **upcoming}

    def _prefetch_next(self, media):
        upcoming = self._safe(self._next_of, media)
        if upcoming:
            self._safe(self._prefetch, {**media, **upcoming})

    def _wait_next(self, session):
        """The countdown after an episode, then the next one on the same TV.
        « Arrêter » cancels it; « Lecture » starts it at once."""
        for command in self._pop() or []:
            try:
                self.control(command.get("action"), command.get("value"))
            except Exception:
                pass
        pending = session["next"]
        if session["ended"] or not pending or self._clock() < pending["at"] or pending.get("finding"):
            return
        pending["finding"] = True
        media = pending["media"]
        found = self._safe(self._resolve, dict(media))
        if session["ended"] or self.session is not session:
            return  # stopped during the search
        if not found or not found[0]:
            session["next"] = None
            session["error"] = f"{self._episode_code(media)} introuvable ou pas encore sorti."
            self._end(session, keep_error=True)
            return
        url, filename = found
        title = f"{media.get('name') or 'Série'} — {self._episode_code(media)}"
        try:
            self.start(session["device"], url, title, media, filename)
        except Exception:
            session["next"] = None
            session["error"] = "La TV n'a pas pu lancer l'épisode suivant."
            self._end(session, keep_error=True)

    def _end(self, session, keep_error=False):
        self._save(session, force=True)
        session["ended"] = True
        with self._lock:
            if self.session is session and not keep_error:
                self.session = None
        self._state({"playing": False, "paused": True})
        if self._presence:
            try:
                self._presence(None)
            except Exception:
                pass

    def _share(self, session):
        media = session["media"]
        code = self._episode_code(media)
        paused = session["state"] != "PLAYING"
        self._state({
            "playing": True, "paused": paused, "title": session["title"], "sub_title": code,
            "media_type": media.get("type") or "movie", "poster": media.get("poster") or "",
            "current_time": session["position"], "duration": session["duration"],
        })
        if self._presence:
            try:
                self._presence(session)
            except Exception:
                pass

    def _save(self, session, force=False):
        media = session["media"]
        if not media.get("id") or not session["duration"] or not session["played"]:
            return
        now = self._clock()
        if not force and now - session["saved_at"] < SAVE_EVERY:
            return
        session["saved_at"] = now
        entry = {
            "id": media["id"], "name": media.get("name") or session["title"], "type": media.get("type") or "movie",
            "year": media.get("year") or "", "poster": media.get("poster") or "",
            "season": media.get("season"), "episode": media.get("episode"), "filename": session["filename"] or session["title"],
            "position": round(session["position"]), "duration": round(session["duration"]),
        }
        try:
            self._record(entry)
        except Exception:
            pass
