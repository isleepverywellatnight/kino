"""Release channel: GitHub release parsing, downloads and Windows installer signatures."""
import base64
import io
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "tools"))

import build_update_package  # noqa: E402
import kino_update  # noqa: E402
from kino_version import APP_VERSION, BUNDLE_ID, DATA_SCHEMA, UPDATE_FORMAT, UPDATE_REPO  # noqa: E402

DOWNLOAD = f"https://github.com/{UPDATE_REPO}/releases/download/v9.0.0/"


def next_version():
    major, minor, patch = kino_update.version_tuple(APP_VERSION)
    return f"{major}.{minor}.{patch + 1}"


def release_json(version, assets):
    return {
        "tag_name": f"v{version}", "draft": False, "prerelease": False, "body": "Notes",
        "html_url": f"https://github.com/{UPDATE_REPO}/releases/tag/v{version}",
        "assets": [{"name": name, "browser_download_url": DOWNLOAD + name, "size": size} for name, size in assets],
    }


def test_parse_release_keeps_only_this_repository_assets():
    data = release_json("9.0.0", [("KINO-9.0.0-arm64.kinoupdate", 10)])
    data["assets"].append({"name": "evil.exe", "browser_download_url": "https://example.com/evil.exe", "size": 5})
    release = kino_update.parse_release(data)
    assert release["version"] == "9.0.0"
    assert set(release["assets"]) == {"KINO-9.0.0-arm64.kinoupdate"}
    assert release["page"].endswith("/releases/tag/v9.0.0")


@pytest.mark.parametrize("data", [
    {"tag_name": "v9.0.0", "prerelease": True},
    {"tag_name": "v9.0.0", "draft": True},
    {"tag_name": "latest"},
    None,
])
def test_parse_release_refuses_unpublished_or_unversioned(data):
    with pytest.raises(kino_update.UpdateError):
        kino_update.parse_release(data)


def test_asset_names():
    assert kino_update.release_asset_names("1.2.3", "darwin", "arm64") == ("KINO-1.2.3-arm64.kinoupdate", None)
    assert kino_update.release_asset_names("1.2.3", "win32") == ("KINO-Windows.exe", "KINO-Windows.exe.sig")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_download_asset_checks_size(tmp_path):
    body = b"x" * 3000
    opener = lambda url, **kw: FakeResponse(body)  # noqa: E731
    target = kino_update.download_asset({"url": DOWNLOAD + "a", "size": 3000}, tmp_path / "a", opener=opener)
    assert target.read_bytes() == body
    with pytest.raises(kino_update.UpdateError):
        kino_update.download_asset({"url": DOWNLOAD + "b", "size": 10}, tmp_path / "b", opener=opener)
    assert not (tmp_path / "b").exists() and not (tmp_path / "b.part").exists()
    with pytest.raises(kino_update.UpdateError):
        kino_update.download_asset({"url": "https://example.com/a", "size": 3000}, tmp_path / "c", opener=opener)


@pytest.fixture
def signing_key(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    real_read = Path.read_text

    def read_text(self, *args, **kwargs):  # the bundled public key, as the test key
        if self.name == "update_public_key.hex":
            return public + "\n"
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return key, key_path, public


def test_installer_signature_round_trip(tmp_path, signing_key):
    _, key_path, public = signing_key
    installer = tmp_path / "KINO-Windows.exe"
    installer.write_bytes(b"MZ installer")
    signature = build_update_package.sign_installer(installer, key_path, "Notes")
    payload = kino_update.verify_installer_signature(signature.read_bytes(), public, current_version="0.0.1")
    assert payload["artifact"]["name"] == installer.name
    assert payload["artifact"]["size"] == installer.stat().st_size
    # Not newer than the running KINO: refused.
    with pytest.raises(kino_update.UpdateError):
        kino_update.verify_installer_signature(signature.read_bytes(), public)


def test_installer_signature_refuses_tampering(signing_key):
    key, _, public = signing_key
    payload = {
        "format": UPDATE_FORMAT, "version": "9.0.0", "platform": "win32", "architecture": "x64",
        "bundle_id": BUNDLE_ID, "data_schema": DATA_SCHEMA, "notes": "",
        "artifact": {"name": "KINO-Windows.exe", "sha256": "a" * 64, "size": 10},
    }
    signature = base64.b64encode(key.sign(kino_update.canonical_json(payload))).decode()
    payload["artifact"]["size"] = 11
    raw = json.dumps({"payload": payload, "signature": signature}).encode()
    with pytest.raises(kino_update.UpdateError):
        kino_update.verify_installer_signature(raw, public)


def test_windows_check_downloads_and_verifies(tmp_path, signing_key, monkeypatch):
    key, _, public = signing_key
    version = next_version()
    installer = b"MZ" + b"k" * 5000
    import hashlib
    payload = {
        "format": UPDATE_FORMAT, "version": version, "platform": "win32", "architecture": "x64",
        "bundle_id": BUNDLE_ID, "data_schema": DATA_SCHEMA, "notes": "",
        "artifact": {"name": "KINO-Windows.exe", "sha256": hashlib.sha256(installer).hexdigest(),
                     "size": len(installer)},
    }
    sig = kino_update.canonical_json({"payload": payload, "signature": base64.b64encode(
        key.sign(kino_update.canonical_json(payload))).decode()})
    files = {"KINO-Windows.exe": installer, "KINO-Windows.exe.sig": sig}
    release = kino_update.parse_release(release_json(version, [(n, len(b)) for n, b in files.items()]))

    monkeypatch.setattr(kino_update.sys, "platform", "win32")
    monkeypatch.setattr(kino_update, "_open_url", None)
    real_download = kino_update.download_asset

    def download(asset, target, **kwargs):
        name = asset["url"].rsplit("/", 1)[1]
        kwargs["opener"] = lambda url, **kw: FakeResponse(files[name])
        return real_download(asset, target, **kwargs)

    monkeypatch.setattr(kino_update, "download_asset", download)
    manager = kino_update.UpdateManager(app_path=tmp_path / "app", state_dir=tmp_path / "state", public_key_hex=public)
    monkeypatch.setattr(manager, "is_installed", lambda: True)
    result = manager.check_for_updates(fetch=lambda: release)
    assert result["available"], result["message"]
    assert result["candidate"]["version"] == version
    assert result["latest"]["version"] == version


def test_up_to_date_message(tmp_path, monkeypatch):
    monkeypatch.setattr(kino_update.sys, "platform", "win32")
    manager = kino_update.UpdateManager(app_path=tmp_path, state_dir=tmp_path / "state", public_key_hex="00" * 32)
    release = kino_update.parse_release(release_json(APP_VERSION, []))
    result = manager.check_for_updates(fetch=lambda: release)
    assert not result["available"] and "à jour" in result["message"]
    assert result["latest"] is None
