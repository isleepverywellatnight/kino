"""Authenticated KINO updates, from GitHub Releases or a local package.

The public key is bundled with KINO. The matching private key is never bundled:
the release workflow signs with it (see .github/workflows/build.yml).

macOS: a signed .kinoupdate package (downloaded from the latest release, or
found locally) is verified, staged, then installed by the native helper, which
keeps the previous version for a rollback.
Windows: the latest release's installer is downloaded with its signed
description (.sig), checked against it, then run silently; it waits for KINO to
quit and relaunches it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import sqlite3
import ssl
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kino_version import APP_VERSION, BUNDLE_ID, DATA_SCHEMA, UPDATE_FORMAT, UPDATE_REPO

MAX_PACKAGE_BYTES = 900 * 1024 * 1024
MAX_APP_BYTES = 1800 * 1024 * 1024
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_ARTIFACT_RE = re.compile(r"^KINO-[0-9]+\.[0-9]+\.[0-9]+-(arm64|x86_64)\.tar\.gz$")
RELEASES_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"
_RELEASE_PAGE_PREFIX = f"https://github.com/{UPDATE_REPO}/releases/"
_DOWNLOAD_PREFIX = f"https://github.com/{UPDATE_REPO}/releases/download/"
MAX_RELEASE_JSON = 2_000_000
# Release files with the same name for every version: the download links never change.
WINDOWS_INSTALLER = "KINO-Windows.exe"
MACOS_DISK_IMAGE = "KINO-macOS.dmg"
MAX_SIGNATURE_BYTES = 32_768


class UpdateError(ValueError):
    pass


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def version_tuple(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise UpdateError("Version du paquet invalide.")
    return tuple(int(value) for value in match.groups())


def _public_key_path() -> Path:
    import paths
    return paths.resource("assets", "update_public_key.hex")


def _signed_payload(raw: bytes, public_key_hex: str):
    """The payload of a signed envelope {"payload": ..., "signature": base64}, once its signature checks."""
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise UpdateError("Manifeste trop volumineux.")
    try:
        envelope = json.loads(raw)
        payload = envelope["payload"]
        signature = base64.b64decode(envelope["signature"], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex.strip()))
        public_key.verify(signature, canonical_json(payload))
    except (ValueError, KeyError, TypeError, InvalidSignature) as exc:
        raise UpdateError("Signature du manifeste invalide.") from exc
    return payload


def verify_installer_signature(raw: bytes, public_key_hex: str, *, current_version: str = APP_VERSION) -> dict:
    """The signed description of the Windows installer (KINO-Windows.exe.sig)."""
    payload = _signed_payload(raw, public_key_hex)
    if not isinstance(payload, dict) or set(payload) != {
        "format", "version", "platform", "architecture", "bundle_id", "data_schema", "notes", "artifact",
    }:
        raise UpdateError("Champs de la signature invalides.")
    if (payload["format"] != UPDATE_FORMAT or payload["platform"] != "win32"
            or payload["architecture"] != "x64" or payload["bundle_id"] != BUNDLE_ID):
        raise UpdateError("Cet installateur ne cible pas KINO sur Windows.")
    if version_tuple(payload["version"]) <= version_tuple(current_version):
        raise UpdateError("Cet installateur n'est pas plus récent que KINO.")
    if type(payload["data_schema"]) is not int or payload["data_schema"] < DATA_SCHEMA:
        raise UpdateError("La base de données de cette version est plus ancienne.")
    if not isinstance(payload["notes"], str) or len(payload["notes"]) > 4000:
        raise UpdateError("Notes de version invalides.")
    artifact = payload["artifact"]
    if not isinstance(artifact, dict) or set(artifact) != {"name", "sha256", "size"}:
        raise UpdateError("Artefact invalide.")
    if artifact["name"] != WINDOWS_INSTALLER:
        raise UpdateError("Nom d'installateur incohérent.")
    if not isinstance(artifact["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
        raise UpdateError("Empreinte d'installateur invalide.")
    if type(artifact["size"]) is not int or not 0 < artifact["size"] <= MAX_PACKAGE_BYTES:
        raise UpdateError("Taille d'installateur invalide.")
    return payload


def _https_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _open_url(url: str, *, timeout: float = 20, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"User-Agent": f"KINO/{APP_VERSION}", "Accept": accept})
    return urllib.request.urlopen(request, timeout=timeout, context=_https_context())


def parse_release(data) -> dict:
    """The parts of a GitHub release KINO uses: version, notes, page and downloadable assets."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("Aucune version publiée.")
    version = str(data.get("tag_name") or "").removeprefix("v")
    version_tuple(version)
    assets = {}
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name, url, size = asset.get("name"), asset.get("browser_download_url"), asset.get("size")
        if (isinstance(name, str) and isinstance(url, str) and url.startswith(_DOWNLOAD_PREFIX)
                and type(size) is int and 0 < size <= MAX_PACKAGE_BYTES):
            assets[name] = {"url": url, "size": size}
    page = data.get("html_url")
    if not isinstance(page, str) or not page.startswith(_RELEASE_PAGE_PREFIX):
        page = RELEASES_PAGE
    notes = data.get("body") if isinstance(data.get("body"), str) else ""
    return {"version": version, "notes": notes[:4000], "page": page, "assets": assets}


def fetch_latest_release(opener=_open_url) -> dict:
    with opener(RELEASES_API) as response:
        raw = response.read(MAX_RELEASE_JSON + 1)
    if len(raw) > MAX_RELEASE_JSON:
        raise UpdateError("Réponse de GitHub trop volumineuse.")
    try:
        return parse_release(json.loads(raw))
    except ValueError as exc:
        raise UpdateError("Réponse de GitHub illisible.") from exc


def release_asset_names(version: str, platform_name: str | None = None,
                        architecture: str | None = None) -> tuple[str, str | None]:
    """(file to download, its separate signature or None) for this system."""
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return f"KINO-{version}-{architecture or platform.machine()}.kinoupdate", None
    if platform_name == "win32":
        return WINDOWS_INSTALLER, f"{WINDOWS_INSTALLER}.sig"
    raise UpdateError("Les mises à jour automatiques ne sont pas disponibles sur ce système.")


def download_asset(asset: dict, target: Path, *, progress=None, opener=_open_url) -> Path:
    """Download a release asset to target, refusing anything but its announced size."""
    url, size = asset["url"], asset["size"]
    if not url.startswith(_DOWNLOAD_PREFIX) or type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES:
        raise UpdateError("Adresse de téléchargement refusée.")
    part = target.with_name(target.name + ".part")
    copied = 0
    try:
        with opener(url, timeout=60, accept="application/octet-stream") as response, part.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                copied += len(chunk)
                if copied > size:
                    raise UpdateError("Le fichier téléchargé est plus gros qu'annoncé.")
                out.write(chunk)
                if progress:
                    progress(copied / size)
        if copied != size:
            raise UpdateError("Téléchargement incomplet.")
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def default_state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KINO" / "Updates"
    return Path.home() / "Library/Application Support/KINO/Updates"


def default_app_path() -> Path:
    """KINO.app on macOS, the installation folder (holding KINO.exe) on Windows."""
    executable = Path(sys.executable).resolve()
    return executable.parent if sys.platform == "win32" else executable.parent.parent.parent


def verify_manifest(raw: bytes, public_key_hex: str, *, current_version: str = APP_VERSION,
                    architecture: str | None = None, os_version: str | None = None) -> dict:
    payload = _signed_payload(raw, public_key_hex)
    if not isinstance(payload, dict) or set(payload) != {
        "format", "version", "platform", "architecture", "bundle_id", "data_schema",
        "min_macos", "notes", "artifact",
    }:
        raise UpdateError("Champs du manifeste invalides.")
    if payload["format"] != UPDATE_FORMAT or payload["platform"] != "darwin" or payload["bundle_id"] != BUNDLE_ID:
        raise UpdateError("Ce paquet ne cible pas KINO sur macOS.")
    arch = architecture or platform.machine()
    if payload["architecture"] != arch:
        raise UpdateError(f"Paquet pour {payload['architecture']}, Mac actuel : {arch}.")
    if version_tuple(payload["version"]) <= version_tuple(current_version):
        raise UpdateError("Ce paquet n'est pas plus récent que KINO.")
    if type(payload["data_schema"]) is not int or payload["data_schema"] != DATA_SCHEMA:
        raise UpdateError("La base de données de cette version n'est pas compatible avec le retour arrière.")
    if not isinstance(payload["notes"], str) or len(payload["notes"]) > 4000:
        raise UpdateError("Notes de version invalides.")
    min_os = payload["min_macos"]
    if not isinstance(min_os, str) or not re.fullmatch(r"\d+\.\d+", min_os):
        raise UpdateError("Version macOS minimale invalide.")
    actual_os = os_version or platform.mac_ver()[0]
    if actual_os and tuple(map(int, actual_os.split(".")[:2])) < tuple(map(int, min_os.split("."))):
        raise UpdateError(f"Ce paquet exige macOS {min_os} ou plus récent.")
    artifact = payload["artifact"]
    if not isinstance(artifact, dict) or set(artifact) != {"name", "sha256", "size"}:
        raise UpdateError("Artefact invalide.")
    if not isinstance(artifact["name"], str) or not _ARTIFACT_RE.fullmatch(artifact["name"]):
        raise UpdateError("Nom d'artefact invalide.")
    if artifact["name"] != f"KINO-{payload['version']}-{arch}.tar.gz":
        raise UpdateError("Nom d'artefact incohérent.")
    if not isinstance(artifact["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
        raise UpdateError("Empreinte d'artefact invalide.")
    if type(artifact["size"]) is not int or not 0 < artifact["size"] <= MAX_PACKAGE_BYTES:
        raise UpdateError("Taille d'artefact invalide.")
    return payload


def _safe_tar_members(archive: tarfile.TarFile) -> None:
    total = 0
    seen: set[str] = set()
    for member in archive.getmembers():
        if len(seen) >= 100_000:
            raise UpdateError("L'archive contient trop de fichiers.")
        name = member.name.removeprefix("./")
        parts = Path(name).parts
        if (not parts or parts[0] != "KINO.app" or os.path.normpath(name) != name
                or any(part in ("", ".", "..") for part in parts)):
            raise UpdateError("L'archive contient un chemin interdit.")
        if name in seen:
            raise UpdateError("L'archive contient des chemins dupliqués.")
        seen.add(name)
        if not (member.isfile() or member.isdir() or member.issym()):
            raise UpdateError("Type de fichier interdit dans l'archive.")
        if member.issym():
            target = Path(name).parent / member.linkname
            normalized = os.path.normpath(str(target))
            if not normalized.startswith("KINO.app/") or os.path.isabs(member.linkname):
                raise UpdateError("Lien symbolique hors de l'application.")
        total += member.size
        if total > MAX_APP_BYTES:
            raise UpdateError("Application décompressée trop volumineuse.")


def _bundle_version(app_path: Path) -> str:
    try:
        with (app_path / "Contents/Info.plist").open("rb") as fh:
            info = plistlib.load(fh)
    except (OSError, ValueError) as exc:
        raise UpdateError("Info.plist du paquet illisible.") from exc
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "KINO":
        raise UpdateError("Identité de l'application incorrecte.")
    return info.get("CFBundleShortVersionString", "")


def _run_checked(args: list[str], timeout: int = 30) -> None:
    try:
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Vérification de l'application échouée : {Path(args[0]).name}.") from exc


def verify_candidate(app_path: Path, payload: dict, *, run_self_check: bool = True) -> None:
    if _bundle_version(app_path) != payload["version"]:
        raise UpdateError("La version de l'application diffère du manifeste.")
    executable = app_path / "Contents/MacOS/KINO"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise UpdateError("Exécutable KINO manquant.")
    try:
        arches = subprocess.check_output(["/usr/bin/lipo", "-archs", str(executable)], text=True, timeout=10).split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Architecture de l'exécutable illisible.") from exc
    if payload["architecture"] not in arches:
        raise UpdateError("L'exécutable n'a pas la bonne architecture.")
    _run_checked(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_path)])
    if run_self_check:
        _run_checked([str(executable), "--self-check"], timeout=45)


def _regular_package(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise UpdateError("Paquet introuvable.") from exc
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PACKAGE_BYTES:
        raise UpdateError("Paquet invalide ou trop volumineux.")


def _signed_package_contents(package: zipfile.ZipFile, public_key_hex: str, *,
                             current_version: str, architecture: str | None = None) -> tuple[dict, zipfile.ZipInfo]:
    names = package.namelist()
    if len(names) != 2 or names.count("manifest.json") != 1:
        raise UpdateError("Structure du paquet invalide.")
    manifest_info = package.getinfo("manifest.json")
    if manifest_info.file_size > 32_768:
        raise UpdateError("Manifeste trop volumineux.")
    with package.open(manifest_info) as manifest_stream:
        manifest_raw = manifest_stream.read(32_769)
    payload = verify_manifest(manifest_raw, public_key_hex,
                              current_version=current_version, architecture=architecture)
    artifact = payload["artifact"]
    if names.count(artifact["name"]) != 1:
        raise UpdateError("Artefact absent du paquet.")
    archive_info = package.getinfo(artifact["name"])
    if archive_info.file_size != artifact["size"]:
        raise UpdateError("Taille de l'artefact incorrecte.")
    return payload, archive_info


def inspect_package(package_path: Path, public_key_hex: str, *,
                    current_version: str = APP_VERSION, architecture: str | None = None) -> dict:
    """Read only signed metadata for local discovery; no bundle is staged yet."""
    package_path = Path(package_path)
    _regular_package(package_path)
    try:
        with zipfile.ZipFile(package_path) as package:
            payload, _ = _signed_package_contents(package, public_key_hex,
                                                  current_version=current_version,
                                                  architecture=architecture)
            return payload
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError("Paquet de mise à jour illisible.") from exc


def local_update_candidates(directories: list[tuple[Path, str]], public_key_hex: str, *,
                            current_version: str = APP_VERSION, architecture: str | None = None) -> list[tuple[Path, dict, str]]:
    """Find compatible signed packages in shallow local directories, newest first."""
    found: list[tuple[Path, dict, str, float]] = []
    for directory, label in directories:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not (entry.name.startswith("KINO-") and entry.name.endswith(".kinoupdate")
                            and entry.is_file(follow_symlinks=False)):
                        continue
                    path = Path(entry.path)
                    try:
                        payload = inspect_package(path, public_key_hex,
                                                  current_version=current_version,
                                                  architecture=architecture)
                        found.append((path, payload, label, entry.stat(follow_symlinks=False).st_mtime))
                    except (OSError, UpdateError):
                        continue
        except OSError:
            continue
    found.sort(key=lambda item: (version_tuple(item[1]["version"]), item[3]), reverse=True)
    return [(path, payload, label) for path, payload, label, _ in found]


def verify_and_extract(package_path: Path, stage_dir: Path, public_key_hex: str, *,
                       current_version: str = APP_VERSION, architecture: str | None = None,
                       progress=None, run_self_check: bool = True) -> dict:
    package_path = Path(package_path)
    _regular_package(package_path)
    stage_dir.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(package_path) as package:
            payload, archive_info = _signed_package_contents(package, public_key_hex,
                                                              current_version=current_version,
                                                              architecture=architecture)
            artifact = payload["artifact"]
            archive_path = stage_dir / artifact["name"]
            digest = hashlib.sha256()
            copied = 0
            with package.open(archive_info) as source, archive_path.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    if progress:
                        progress(min(70, round(copied / artifact["size"] * 70)))
            if copied != artifact["size"] or digest.hexdigest() != artifact["sha256"]:
                raise UpdateError("L'artefact est tronqué ou modifié.")
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_tar_members(archive)
            archive.extractall(stage_dir, filter="data")
        archive_path.unlink()
        if progress:
            progress(85)
        candidate = stage_dir / "KINO.app"
        verify_candidate(candidate, payload, run_self_check=run_self_check)
        if progress:
            progress(100)
        return {"payload": payload, "candidate": str(candidate)}
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def snapshot_database(db_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not db_path.exists():
        return
    source = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    destination.chmod(0o600)


def snapshot_user_data(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    destination.chmod(0o700)
    snapshot_database(Path.home() / ".kino_data.db", destination / "kino_data.db")
    for name in (".rd_cinehub_config.json", ".kino_playlist.m3u"):
        source = Path.home() / name
        if source.is_file():
            target = destination / name
            shutil.copy2(source, target)
            target.chmod(0o600)


class UpdateManager:
    def __init__(self, *, app_path: Path | None = None, state_dir: Path | None = None,
                 public_key_hex: str | None = None):
        self.app_path = app_path or default_app_path()
        self.state_dir = state_dir or default_state_dir()
        self.public_key_hex = public_key_hex
        self._lock = threading.RLock()
        self._progress = 0
        self._progress_label = ""
        self._message = ""
        self._candidate: dict | None = None
        self._latest: dict | None = None

    def is_installed(self) -> bool:
        """True for the copy of KINO that updates may replace."""
        if sys.platform == "darwin":
            return self.app_path == Path("/Applications/KINO.app") and self.app_path.is_dir()
        if sys.platform == "win32":
            # Installed by packaging/kino.iss, which leaves its uninstaller next to KINO.exe.
            return bool(getattr(sys, "frozen", False)) and (self.app_path / "unins000.exe").is_file()
        return False

    def _require_installed(self) -> None:
        if sys.platform == "darwin":
            if not self.is_installed():
                raise UpdateError("Ouvrez d'abord KINO depuis /Applications/KINO.app.")
        elif sys.platform == "win32":
            if not self.is_installed():
                raise UpdateError("Installez KINO avec son installateur pour recevoir les mises à jour.")
        else:
            raise UpdateError("Les mises à jour automatiques ne sont pas disponibles sur ce système.")

    def _public_key(self) -> str:
        return self.public_key_hex or _public_key_path().read_text(encoding="ascii")

    def _newer_latest_locked(self) -> dict | None:
        latest = self._latest
        if latest and version_tuple(latest["version"]) > version_tuple(APP_VERSION):
            return latest
        return None

    def _discard_candidate_locked(self) -> None:
        if self._candidate:
            stage = Path(self._candidate["candidate"]).parent
            if stage.parent == self.state_dir and stage.name.startswith("stage-"):
                shutil.rmtree(stage, ignore_errors=True)
        self._candidate = None

    def _read_state(self) -> dict:
        try:
            return json.loads((self.state_dir / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self) -> dict:
        with self._lock:
            state = self._read_state()
            backup_path = Path(state.get("backup_path") or "/nonexistent")
            latest = self._newer_latest_locked()
            return {
                "version": APP_VERSION,
                "platform": sys.platform,
                "channel": "github",
                "remote_configured": True,
                "installed": self.is_installed(),
                "local_packages": sys.platform == "darwin",
                "rollback_supported": sys.platform == "darwin",
                "latest": {key: latest[key] for key in ("version", "notes", "page")} if latest else None,
                "candidate": self._candidate["payload"] if self._candidate else None,
                "progress": self._progress,
                "progress_label": self._progress_label,
                "message": self._message or state.get("message", ""),
                "rollback_available": (sys.platform == "darwin" and backup_path.is_dir()
                                       and state.get("data_schema") == DATA_SCHEMA),
                "rollback_version": state.get("backup_version", ""),
            }

    def check_latest(self, fetch=fetch_latest_release) -> dict:
        """Ask GitHub for the latest release; remembered for status()."""
        release = fetch()
        with self._lock:
            self._latest = release
        return release

    def check_latest_quietly(self, delay: float = 20.0) -> None:
        """Startup: learn about a new version in the background; failures stay silent."""
        time.sleep(delay)
        try:
            self.check_latest()
        except Exception:
            pass

    def check_for_updates(self, fetch=fetch_latest_release) -> dict:
        """The « Rechercher une mise à jour » button: GitHub first, then (macOS) local packages."""
        with self._lock:
            self._discard_candidate_locked()
            self._progress = 0
            self._progress_label = ""
            self._message = "Recherche d'une nouvelle version…"
        try:
            release = self.check_latest(fetch)
        except Exception:
            release = None
        newer = bool(release) and version_tuple(release["version"]) > version_tuple(APP_VERSION)
        if sys.platform == "win32":
            return self._check_windows(release, newer)
        download_note = ""
        if newer and sys.platform == "darwin":
            try:
                self._download_mac_package(release)
            except (OSError, UpdateError) as exc:
                download_note = str(exc)
        try:
            result = self.check_local_updates()
        except UpdateError as exc:
            result = {**self.status(), "available": False}
            download_note = download_note or str(exc)
        if result.get("available"):
            return result
        with self._lock:
            if newer:
                self._message = (f"KINO {release['version']} est disponible. {download_note}".strip()
                                 + " Vous pouvez aussi le télécharger depuis la page des versions.")
            elif release:
                self._message = f"KINO est à jour (version {APP_VERSION})."
            else:
                self._message = "Impossible de joindre GitHub. " + self._message
        return {**self.status(), "available": False, "message": self._message}

    def _download_progress(self, label: str):
        def update(fraction: float) -> None:
            with self._lock:
                self._progress_label = label
                self._progress = max(1, min(99, round(fraction * 100)))
        return update

    def _download_mac_package(self, release: dict) -> None:
        self._require_installed()
        name, _ = release_asset_names(release["version"])
        asset = release["assets"].get(name)
        if not asset:
            raise UpdateError("Cette version n'a pas de paquet de mise à jour pour ce Mac.")
        inbox = self.state_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        inbox.chmod(0o700)
        target = inbox / name
        if target.is_file() and target.stat().st_size == asset["size"]:
            return
        with self._lock:
            self._message = f"Téléchargement de KINO {release['version']}…"
        download_asset(asset, target, progress=self._download_progress("Téléchargement"))

    def _check_windows(self, release: dict | None, newer: bool) -> dict:
        if not release:
            message = "Impossible de joindre GitHub. Vérifiez la connexion Internet."
        elif not newer:
            message = f"KINO est à jour (version {APP_VERSION})."
        else:
            try:
                self._require_installed()
                self._prepare_windows(release)
                with self._lock:
                    self._message = f"KINO {release['version']} téléchargé et vérifié. Installation prête."
                    self._progress, self._progress_label = 100, ""
                return {**self.status(), "available": True}
            except (OSError, UpdateError) as exc:
                message = (f"KINO {release['version']} est disponible. {exc} "
                           "Vous pouvez aussi le télécharger depuis la page des versions.")
        with self._lock:
            self._message = message
            self._progress, self._progress_label = 0, ""
        return {**self.status(), "available": False, "message": message}

    def _prepare_windows(self, release: dict) -> None:
        name, signature_name = release_asset_names(release["version"], "win32")
        asset, signature_asset = release["assets"].get(name), release["assets"].get(signature_name)
        if not asset or not signature_asset:
            raise UpdateError("Cette version n'est pas signée pour les mises à jour automatiques.")
        if signature_asset["size"] > MAX_SIGNATURE_BYTES:
            raise UpdateError("Signature trop volumineuse.")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        stage = self.state_dir / f"stage-{secrets.token_hex(8)}"
        stage.mkdir()
        try:
            signature_path = download_asset(signature_asset, stage / signature_name)
            payload = verify_installer_signature(signature_path.read_bytes(), self._public_key())
            if payload["version"] != release["version"] or payload["artifact"]["name"] != name \
                    or payload["artifact"]["size"] != asset["size"]:
                raise UpdateError("La signature ne correspond pas à cette version.")
            with self._lock:
                self._message = f"Téléchargement de KINO {release['version']}…"
            installer = download_asset(asset, stage / name, progress=self._download_progress("Téléchargement"))
            if _sha256_file(installer) != payload["artifact"]["sha256"]:
                raise UpdateError("L'installateur téléchargé est modifié ou incomplet.")
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        with self._lock:
            self._discard_candidate_locked()
            self._candidate = {"payload": payload, "candidate": str(installer), "installer": str(installer)}

    def _install_windows(self) -> dict:
        with self._lock:
            if not self._candidate or "installer" not in self._candidate:
                raise UpdateError("Recherchez d'abord une mise à jour.")
            installer = Path(self._candidate["installer"])
            if _sha256_file(installer) != self._candidate["payload"]["artifact"]["sha256"]:
                raise UpdateError("L'installateur a changé depuis sa vérification.")
            # Detached: the installer waits for KINO to quit (/UPDATE=1), installs, then relaunches it.
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"],
                             creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._message = "KINO va fermer, s'installer puis redémarrer automatiquement."
            return {"scheduled": True, "message": self._message}

    def prepare(self, package_path: Path) -> dict:
        if sys.platform != "darwin":
            raise UpdateError("Les paquets locaux ne concernent que KINO sur macOS.")
        self._require_installed()
        with self._lock:
            self._discard_candidate_locked()
            self._progress = 0
            self._progress_label = "Vérification du paquet"
            self._message = "Vérification de la signature et de l'application…"
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        public_key = self._public_key()
        stage = self.state_dir / f"stage-{secrets.token_hex(8)}"
        try:
            result = verify_and_extract(package_path, stage, public_key,
                                        progress=lambda value: self._set_progress(value))
        except Exception as exc:
            with self._lock:
                self._message = str(exc)
            raise
        with self._lock:
            self._candidate = result
            self._message = "Paquet vérifié. Installation prête."
        return self.status()

    def check_local_updates(self) -> dict:
        if sys.platform != "darwin":
            raise UpdateError("Les paquets locaux ne concernent que KINO sur macOS.")
        self._require_installed()
        with self._lock:
            self._discard_candidate_locked()
            self._progress = 0
            self._message = "Recherche des mises à jour locales signées…"
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        inbox = self.state_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        inbox.chmod(0o700)
        public_key = self._public_key()
        directories = [(Path.home() / "Downloads", "Téléchargements"), (inbox, "dossier KINO")]
        candidates = local_update_candidates(directories, public_key)
        for path, payload, label in candidates:
            try:
                result = self.prepare(path)
                with self._lock:
                    self._message = f"KINO {payload['version']} trouvé dans {label}. Paquet vérifié."
                return {**result, "available": True, "message": self._message}
            except (OSError, UpdateError, zipfile.BadZipFile, tarfile.TarError):
                continue
        with self._lock:
            self._message = ("Aucun nouveau paquet signé trouvé dans Téléchargements ou le dossier KINO. "
                             "Vous pouvez aussi choisir un paquet ailleurs sur ce Mac.")
        return {**self.status(), "available": False, "message": self._message}

    def _set_progress(self, value: int) -> None:
        with self._lock:
            self._progress = value

    def _handoff(self, action: str) -> dict:
        with self._lock:
            if action == "install" and not self._candidate:
                raise UpdateError("Choisissez d'abord un paquet de mise à jour.")
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.state_dir.chmod(0o700)
            helper_source = self.app_path / "Contents/Resources/kino-update-helper"
            if not helper_source.is_file():
                raise UpdateError("Assistant de mise à jour absent de l'application.")
            token = secrets.token_hex(12)
            helper = self.state_dir / f"helper-{token}"
            shutil.copy2(helper_source, helper)
            helper.chmod(0o700)
            if action == "install":
                snapshot_user_data(self.state_dir / f"data-{token}")
            state = self._read_state()
            backup_path = (self.app_path.parent / f".KINO.backup-{token}.app") if action == "install" else Path(state.get("backup_path") or "")
            if action == "rollback" and (not backup_path.is_dir() or state.get("data_schema") != DATA_SCHEMA):
                raise UpdateError("Aucune version précédente compatible n'est disponible.")
            job = {
                "action": action,
                "pid": os.getpid(),
                "app_path": str(self.app_path),
                "candidate_path": self._candidate["candidate"] if self._candidate else "",
                "backup_path": str(backup_path),
                "backup_version": _bundle_version(self.app_path) if action == "install" else state.get("backup_version", ""),
                "version": self._candidate["payload"]["version"] if self._candidate else state.get("backup_version", ""),
                "data_schema": DATA_SCHEMA,
                "status_path": str(self.state_dir / "status.json"),
                "pending_path": str(self.state_dir / "pending.json"),
                "ack_path": str(self.state_dir / f"ack-{token}.json"),
                "nonce": token,
            }
            job_path = self.state_dir / f"job-{token}.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            job_path.chmod(0o600)
            subprocess.Popen([str(helper), str(job_path)], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"scheduled": True, "message": "KINO va fermer puis redémarrer automatiquement."}

    def install(self) -> dict:
        if sys.platform == "win32":
            return self._install_windows()
        return self._handoff("install")

    def rollback(self) -> dict:
        if sys.platform != "darwin":
            raise UpdateError("Le retour à la version précédente n'est disponible que sur macOS.")
        return self._handoff("rollback")


manager = UpdateManager()


KEEP_DATA_SNAPSHOTS = 3
STALE_STAGE_SEC = 10 * 60
STALE_WORK_FILE_SEC = 24 * 3600
_WORK_FILE = re.compile(r"(?:helper|ack|job)-[0-9a-f]{24}(?:\.json)?")


def _package_version(path: Path) -> str:
    """Version announced by a package, read without trusting it (only used to discard)."""
    try:
        with zipfile.ZipFile(path) as package:
            return str(json.loads(package.read("manifest.json"))["payload"]["version"])
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        match = re.fullmatch(r"KINO-(\d+\.\d+\.\d+)-[A-Za-z0-9_]+\.kinoupdate", path.name)
        return match.group(1) if match else ""


def prune_update_leftovers(*, app_path: Path = Path("/Applications/KINO.app"), state_dir: Path | None = None,
                           current_version: str = APP_VERSION, now: float | None = None) -> dict:
    """Remove what finished updates leave behind.

    Kept: the rollback bundle named in status.json plus the newest other backup,
    the newest data snapshots, and anything while an update awaits confirmation.
    Removed: staged apps, old helper/job/ack files, inbox packages that are not
    newer than this version (KINO would refuse them), older snapshots and backups.
    """
    state_dir = state_dir or default_state_dir()
    now = time.time() if now is None else now
    removed = {"stages": 0, "work_files": 0, "packages": 0, "snapshots": 0, "app_backups": 0}
    if not state_dir.is_dir() or (state_dir / "pending.json").exists():
        return removed

    def age(path: Path) -> float:
        try:
            return now - path.lstat().st_mtime
        except OSError:
            return 0.0

    def plain_dir(path: Path) -> bool:
        return path.is_dir() and not path.is_symlink()

    for entry in list(state_dir.iterdir()):
        if entry.name.startswith("stage-") and plain_dir(entry) and age(entry) > STALE_STAGE_SEC:
            shutil.rmtree(entry, ignore_errors=True)
            removed["stages"] += 1
        elif _WORK_FILE.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink() \
                and age(entry) > STALE_WORK_FILE_SEC:
            entry.unlink(missing_ok=True)
            removed["work_files"] += 1

    inbox = state_dir / "inbox"
    if plain_dir(inbox):
        for package in inbox.glob("KINO-*.kinoupdate"):
            if not package.is_file() or package.is_symlink():
                continue
            try:
                stale = version_tuple(_package_version(package)) <= version_tuple(current_version)
            except UpdateError:
                continue
            if stale:
                package.unlink(missing_ok=True)
                removed["packages"] += 1

    snapshots = sorted((p for p in state_dir.glob("data-*") if plain_dir(p)), key=age)
    for snapshot in snapshots[KEEP_DATA_SNAPSHOTS:]:
        shutil.rmtree(snapshot, ignore_errors=True)
        removed["snapshots"] += 1

    try:
        rollback = Path(json.loads((state_dir / "status.json").read_text(encoding="utf-8")).get("backup_path") or "")
    except (OSError, ValueError, AttributeError):
        rollback = Path("")
    backups = sorted((p for p in app_path.parent.glob(".KINO.backup-*.app") if plain_dir(p)), key=age)
    others = [backup for backup in backups if backup != rollback and backup != app_path]
    for backup in others[1:]:
        shutil.rmtree(backup, ignore_errors=True)
        removed["app_backups"] += 1
    return removed


def prune_installed_update_leftovers(*, app_path: Path | None = None, state_dir: Path | None = None,
                                    wait_sec: float = 180.0, poll_sec: float = 2.0) -> dict:
    """Startup hook: only the installed app cleans the shared update folders.

    Right after an update, the helper relaunches KINO and keeps pending.json until the
    new window confirms it started. Cleaning at that moment was skipped every time, so
    wait (in a background thread) for the confirmation to finish first.
    """
    try:
        app_path = app_path or manager.app_path
        state_dir = state_dir or manager.state_dir
        if app_path is manager.app_path and not manager.is_installed():
            return {}
        deadline = time.monotonic() + wait_sec
        while (state_dir / "pending.json").exists() and time.monotonic() < deadline:
            time.sleep(poll_sec)
        return prune_update_leftovers(app_path=app_path, state_dir=state_dir)
    except Exception:
        return {}


def acknowledge_ready() -> None:
    """Called when the native WebView has loaded after a helper-launched update."""
    if sys.platform != "darwin":
        return
    state_dir = default_state_dir()
    pending = state_dir / "pending.json"
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
        if data.get("version") != _bundle_version(Path(sys.executable).resolve().parent.parent.parent):
            return
        ack = Path(data["ack_path"])
        if ack.parent != state_dir or not ack.name.startswith("ack-"):
            return
        ack.write_text(json.dumps({"nonce": data["nonce"], "ready": True}), encoding="utf-8")
    except (OSError, KeyError, ValueError):
        return
