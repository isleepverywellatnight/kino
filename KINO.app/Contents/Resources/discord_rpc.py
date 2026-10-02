"""
KINO Discord Rich Presence (RPC) Engine
========================================
Affiche le statut de visionnage en temps réel sur le profil Discord de l'utilisateur :
- Titre du film / série / animé avec saison et numéro d'épisode
- Barre de progression dynamique Discord (temps restant / écoulé)
- Affiche du média (Artwork) et logo KINO
- Compatible Windows (Named Pipe) et macOS / Linux (Unix Domain Socket)
- 100% autonome, zéro dépendance externe (Python standard asyncio & struct)
"""

import asyncio
import json
import logging
import os
import struct
import sys
import threading
import time
from typing import Optional, Dict, Any

logger = logging.getLogger("kino.discord")

# Identifiant client Discord par défaut pour les centres multimédias (IMDb / Cinéma)
DEFAULT_CLIENT_ID = "631379801826918400"

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4


class DiscordRPCClient:
    def __init__(self, client_id: str = DEFAULT_CLIENT_ID):
        self.client_id = client_id
        self.enabled = True
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._last_activity: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self._running = False
        self._queue: Optional[asyncio.Queue] = None

    def set_client_id(self, client_id: str):
        """Met à jour le Client ID Discord et réinitialise la connexion si nécessaire."""
        clean_id = str(client_id or "").strip()
        if not clean_id:
            clean_id = DEFAULT_CLIENT_ID
        if clean_id == self.client_id:
            return
        with self._lock:
            self.client_id = clean_id
            self._connected = False
            if self._writer:
                try:
                    self._writer.close()
                except Exception:
                    pass
                self._writer = None
            self._reader = None
            if self._last_activity and self._loop and self._queue:
                self._loop.call_soon_threadsafe(
                    self._queue.put_nowait,
                    {"action": "update", "activity": self._last_activity}
                )

    def start(self):
        """Démarre la boucle asynchrone Discord RPC dans un thread d'arrière-plan."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="KinoDiscordRPC", daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()
        self._loop.run_until_complete(self._worker())

    async def _find_ipc_socket(self):
        """Trouve le socket ou named pipe Discord actif."""
        if sys.platform == "win32":
            for i in range(10):
                pipe_path = rf"\\.\pipe\discord-ipc-{i}"
                if os.path.exists(pipe_path):
                    return pipe_path
        else:
            # macOS / Linux
            env_paths = [
                os.environ.get("XDG_RUNTIME_DIR", ""),
                os.environ.get("TMPDIR", ""),
                os.environ.get("TMP", ""),
                os.environ.get("TEMP", ""),
                "/tmp",
            ]
            for p in env_paths:
                if not p or not os.path.exists(p):
                    continue
                for i in range(10):
                    sock_path = os.path.join(p, f"discord-ipc-{i}")
                    if os.path.exists(sock_path):
                        return sock_path
        return None

    async def _connect(self) -> bool:
        if self._connected and self._writer and not self._writer.is_closing():
            return True

        ipc_path = await self._find_ipc_socket()
        if not ipc_path:
            return False

        try:
            if sys.platform == "win32":
                self._reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(self._reader)
                transport, _ = await self._loop.create_pipe_connection(lambda: protocol, ipc_path)
                self._writer = asyncio.StreamWriter(transport, protocol, self._reader, self._loop)
            else:
                self._reader, self._writer = await asyncio.open_unix_connection(ipc_path)

            # Handshake
            handshake = json.dumps({"v": 1, "client_id": self.client_id}).encode("utf-8")
            header = struct.pack("<II", OP_HANDSHAKE, len(handshake))
            self._writer.write(header + handshake)
            await self._writer.drain()

            resp_hdr = await asyncio.wait_for(self._reader.readexactly(8), timeout=2.0)
            op, length = struct.unpack("<II", resp_hdr)
            resp_body = await asyncio.wait_for(self._reader.readexactly(length), timeout=2.0)
            resp = json.loads(resp_body.decode("utf-8"))

            if resp.get("evt") == "READY" or "user" in resp.get("data", {}):
                self._connected = True
                user_info = resp.get("data", {}).get("user", {})
                logger.info(f"Connecté à Discord RPC : {user_info.get('username')}")
                return True
        except Exception as e:
            self._connected = False
            self._writer = None
            self._reader = None
        return False

    async def _send_frame(self, data: Dict[str, Any]):
        if not self._connected or not self._writer:
            return
        try:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            header = struct.pack("<II", OP_FRAME, len(raw))
            self._writer.write(header + raw)
            await self._writer.drain()

            # Lecture non bloquante de la réponse Discord
            resp_hdr = await asyncio.wait_for(self._reader.readexactly(8), timeout=1.5)
            op, length = struct.unpack("<II", resp_hdr)
            await asyncio.wait_for(self._reader.readexactly(length), timeout=1.5)
        except Exception:
            self._connected = False
            try:
                if self._writer:
                    self._writer.close()
            except Exception:
                pass
            self._writer = None

    async def _worker(self):
        while self._running:
            try:
                item = await self._queue.get()
                if item is None:
                    break

                action = item.get("action")
                if action == "clear":
                    if self._connected:
                        payload = {
                            "cmd": "SET_ACTIVITY",
                            "args": {"pid": os.getpid(), "activity": None},
                            "nonce": str(time.time()),
                        }
                        await self._send_frame(payload)
                elif action == "update":
                    if not self._connected:
                        await self._connect()
                    if self._connected:
                        activity = item.get("activity")
                        payload = {
                            "cmd": "SET_ACTIVITY",
                            "args": {"pid": os.getpid(), "activity": activity},
                            "nonce": str(time.time()),
                        }
                        await self._send_frame(payload)
                self._queue.task_done()
            except Exception as e:
                logger.debug(f"Erreur Discord RPC worker: {e}")
                await asyncio.sleep(1.0)

    def set_activity(
        self,
        title: str,
        media_type: str = "movie",
        season: Optional[int] = None,
        episode: Optional[int] = None,
        ep_title: Optional[str] = None,
        current_time: float = 0.0,
        duration: float = 0.0,
        poster_url: Optional[str] = None,
        is_paused: bool = False,
    ):
        """Met à jour le statut Discord Rich Presence."""
        if not self.enabled or not title:
            return

        now = int(time.time())
        is_series = (media_type in ("series", "anime", "tv")) or (season is not None or episode is not None)

        if is_series:
            s_str = f"S{int(season):02d}" if season else ""
            e_str = f"E{int(episode):02d}" if episode else ""
            ep_code = f"{s_str}{e_str}".strip()
            details = f"{title} {('• ' + ep_code) if ep_code else ''}".strip()
            state = ep_title if ep_title else ("Animation Japonaise" if media_type == "anime" else "Série TV")
        else:
            details = title
            state = "Film • KINO"

        if is_paused:
            state = f"{state} (En pause)"
            timestamps = None
        else:
            if duration and duration > current_time > 0:
                start_ts = now - int(current_time)
                end_ts = start_ts + int(duration)
                timestamps = {"start": start_ts, "end": end_ts}
            else:
                timestamps = {"start": now}

        assets = {
            "large_image": poster_url if (poster_url and poster_url.startswith("http")) else "large_img",
            "large_text": title[:128],
        }
        if self.client_id == "631379801826918400":
            # Preset IMDb
            assets["small_image"] = "paused" if is_paused else "playing"
            assets["small_text"] = "En pause" if is_paused else "Lecture en cours"
        elif self.client_id == "645028677033132033":
            # Preset Plex
            assets["small_image"] = "pause" if is_paused else "play"
            assets["small_text"] = "Plex Media"
        elif poster_url:
            # Client ID personnalisé KINO
            assets["small_image"] = "kino_logo"
            assets["small_text"] = "KINO Media Center"

        activity = {
            "details": details[:128],
            "state": state[:128],
            "assets": assets,
        }
        if timestamps:
            activity["timestamps"] = timestamps

        with self._lock:
            self._last_activity = activity
            if self._loop and self._queue:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, {"action": "update", "activity": activity})

    def clear(self):
        """Efface l'activité Discord."""
        with self._lock:
            self._last_activity = None
            if self._loop and self._queue:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, {"action": "clear"})


# Instance singleton globale
discord_rpc = DiscordRPCClient()
