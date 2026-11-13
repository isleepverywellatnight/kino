#!/usr/bin/env python3
"""Create a signed, single-file local KINO update package.

The signing key lives outside the project and outside the application bundle.
Public distribution must use a separate production key and Developer ID signing.
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import plistlib
import tarfile
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kino_update import canonical_json, version_tuple
from kino_version import BUNDLE_ID, DATA_SCHEMA, UPDATE_FORMAT


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


def package(app_path: Path, output: Path, key_path: Path, notes: str, min_macos: str) -> None:
    with (app_path / "Contents/Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise SystemExit("Identifiant de bundle incorrect.")
    version = info.get("CFBundleShortVersionString", "")
    version_tuple(version)
    arch = platform.machine()
    archive_name = f"KINO-{version}-{arch}.tar.gz"
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("La clé de signature doit être Ed25519.")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pinned = Path(__file__).with_name("update_public_key.hex").read_text(encoding="ascii").strip()
    if public.hex() != pinned:
        raise SystemExit("La clé privée ne correspond pas à la clé publique de l'app.")
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
    parser.add_argument("--key", type=Path, default=private_key_path())
    parser.add_argument("--notes", default="Améliorations et corrections de KINO.")
    parser.add_argument("--min-macos", default="13.0")
    args = parser.parse_args()
    if args.init_key:
        initialize_key(args.key)
    else:
        if not args.app or not args.output:
            parser.error("--app et --output sont requis pour créer un paquet")
        package(args.app, args.output, args.key, args.notes, args.min_macos)


if __name__ == "__main__":
    main()
