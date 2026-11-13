#!/usr/bin/env python3
"""Sign KINO updates: a macOS .kinoupdate package, or a Windows installer's .sig.

The signing key lives outside the project and outside the application bundle;
the release workflow reads it from the KINO_UPDATE_SIGNING_KEY secret.

  --init-key                          create the key pair (once)
  --app dist/KINO.app --output X      macOS: signed package for the in-app updater
  --installer dist/KINO-Setup-...exe  Windows: writes <installer>.sig next to it
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import plistlib
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kino_update import canonical_json, version_tuple
from kino_version import APP_VERSION, BUNDLE_ID, DATA_SCHEMA, UPDATE_FORMAT


def private_key_path() -> Path:
    return Path.home() / "Library/Application Support/KINO/UpdateSigning/private-key.pem"


def initialize_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        raise SystemExit("La clé locale existe déjà ; aucune rotation automatique.")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as fh:
        fh.write(raw)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    Path(__file__).with_name("update_public_key.hex").write_text(public.hex() + "\n", encoding="ascii")
    print("Clé locale créée. Seule sa partie publique est incluse dans KINO.")


def load_signing_key(key_path: Path) -> Ed25519PrivateKey:
    """The private key, checked against the public key bundled in KINO."""
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("La clé de signature doit être Ed25519.")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pinned = Path(__file__).with_name("update_public_key.hex").read_text(encoding="ascii").strip()
    if public.hex() != pinned:
        raise SystemExit("La clé privée ne correspond pas à la clé publique de l'app.")
    return key


def sign_installer(installer: Path, key_path: Path, notes: str) -> Path:
    match = re.fullmatch(r"KINO-Setup-(\d+\.\d+\.\d+)-x64\.exe", installer.name)
    if not match:
        raise SystemExit("Nom d'installateur attendu : KINO-Setup-x.y.z-x64.exe")
    version = match.group(1)
    version_tuple(version)
    if version != APP_VERSION:
        raise SystemExit(f"L'installateur est en {version}, kino_version.py en {APP_VERSION}.")
    key = load_signing_key(key_path)
    digest = hashlib.sha256()
    with installer.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    payload = {
        "format": UPDATE_FORMAT,
        "version": version,
        "platform": "win32",
        "architecture": "x64",
        "bundle_id": BUNDLE_ID,
        "data_schema": DATA_SCHEMA,
        "notes": notes[:4000],
        "artifact": {"name": installer.name, "sha256": digest.hexdigest(), "size": installer.stat().st_size},
    }
    envelope = {"payload": payload, "signature": base64.b64encode(key.sign(canonical_json(payload))).decode("ascii")}
    output = installer.with_name(installer.name + ".sig")
    output.write_bytes(canonical_json(envelope))
    print(f"Signature de l'installateur : {output}")
    return output


def package(app_path: Path, output: Path, key_path: Path, notes: str, min_macos: str) -> None:
    with (app_path / "Contents/Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise SystemExit("Identifiant de bundle incorrect.")
    version = info.get("CFBundleShortVersionString", "")
    version_tuple(version)
    arch = platform.machine()
    archive_name = f"KINO-{version}-{arch}.tar.gz"
    key = load_signing_key(key_path)
    with tempfile.TemporaryDirectory(prefix="kino-package-") as tmp:
        archive_path = Path(tmp) / archive_name
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(app_path, arcname="KINO.app", recursive=True)
        digest = hashlib.sha256()
        with archive_path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                digest.update(chunk)
        payload = {
            "format": UPDATE_FORMAT,
            "version": version,
            "platform": "darwin",
            "architecture": arch,
            "bundle_id": BUNDLE_ID,
            "data_schema": DATA_SCHEMA,
            "min_macos": min_macos,
            "notes": notes[:4000],
            "artifact": {
                "name": archive_name,
                "sha256": digest.hexdigest(),
                "size": archive_path.stat().st_size,
            },
        }
        envelope = {
            "payload": payload,
            "signature": base64.b64encode(key.sign(canonical_json(payload))).decode("ascii"),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", allowZip64=True) as outer:
            outer.writestr("manifest.json", canonical_json(envelope), compress_type=zipfile.ZIP_STORED)
            outer.write(archive_path, archive_name, compress_type=zipfile.ZIP_STORED)
    print(f"Paquet local signé : {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-key", action="store_true")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--key", type=Path, default=private_key_path())
    parser.add_argument("--notes", default="Améliorations et corrections de KINO.")
    parser.add_argument("--min-macos", default="14.0")
    args = parser.parse_args()
    if args.init_key:
        initialize_key(args.key)
    elif args.installer:
        sign_installer(args.installer, args.key, args.notes)
    else:
        if not args.app or not args.output:
            parser.error("--app et --output sont requis pour créer un paquet")
        package(args.app, args.output, args.key, args.notes, args.min_macos)


if __name__ == "__main__":
    main()
