# KINO のビルドと公開

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · **日本語**

以下は開発者向けの手順です。KINO を使うだけなら、ダウンロードするだけで十分です（[README](README.ja.md) を参照）。

## バージョンの公開

`Cargo.toml` ([workspace.package]) の `version` を変更して `main` に push するだけです。`main` のバージョンにまだリリースがなければ、GitHub Actions（`.github/workflows/build.yml`）が両方のアプリをビルドしてテストし、`vX.Y.Z` タグ付きでリリース（`KINO-macOS.dmg`、`KINO-Windows.exe`、`SHA256SUMS.txt`）を公開したあと、README のコマンドでクリーンな Mac と Windows に再インストールして確認します。

KINO が新しいバージョンを自動でインストールできるよう、CI はシークレット `KINO_UPDATE_SIGNING_KEY`（Settings → Secrets and variables → Actions）でアップデートに署名します。中身は `cargo run -p kino --bin kino-release -- --init-key` で作成した秘密鍵で、公開鍵は `assets/update_public_key.hex` です。このシークレットがない場合、KINO は新しいバージョンを通知してダウンロードを案内します。

リリースはこのリポジトリに公開され、KINO のダウンロード、インストールスクリプト、アップデートはそれを使います。CI は自身の `GITHUB_TOKEN` で公開するため、ほかのシークレットは不要です。

## 手元でビルドする

KINO は Rust（`rust-toolchain.toml` のバージョン）でビルドします。Python 3.12 は mpv と FFmpeg を取得するツールにのみ使います。プレーヤーのライブラリは `vendor/`（git の管理外）にコピーされます。

### macOS (Apple Silicon)

libmpv は `/Applications` にインストールされた [IINA](https://iina.io) から取得します。

```bash
python3.12 -m venv .venv && .venv/bin/pip install imageio-ffmpeg
.venv/bin/python tools/vendor_libmpv.py               # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv は固定バージョンをダウンロードし、チェックサムを検証します。展開には [7-Zip](https://7-zip.org) を使います。インストーラーは [Inno Setup 6](https://jrsoftware.org/isinfo.php) でビルドします。

```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install imageio-ffmpeg
.venv\Scripts\python tools\fetch_mpv_windows.py
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1 (.venv\Scripts\python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

ビルドしたアプリは `--self-check` で中身を確認できます。

## テスト

CI は push のたびに両方を実行します。Windows のテスト（ワークフロー「Windows UI」）は実際の KINO を起動し、WebView2 の DevTools ポート経由で本物のクリックやドラッグで操作して、ウィンドウ、プレーヤー、原題を確認します。

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## 翻訳

インターフェースはフランス語で書かれています。`web/js/i18n.js` が表示時に翻訳し、言語ごとの辞書 `web/js/i18n/<言語>.js` で各フランス語テキストを訳文に対応させます（`{}` は可変部分）。訳がないテキストは英語で、英語の場合はフランス語で表示されます。

```bash
python3 tools/i18n.py extract       # web/ and crates/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

言語を追加するには、他のファイルにならって `web/js/i18n/<コード>.js` を作成し、`LANGUAGES`（`web/js/i18n.js`）と `UI_LANGUAGES`（`crates/kino/src/config.rs`）にコードを追加します。

## `packaging/` の内容

| ファイル | 役割 |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | macOS のディスクイメージ |
| `kino.iss` | Windows のワンクリックインストーラー（Inno Setup） |
| `install.sh`, `install.ps1` | 最新リリースからコマンドひとつでインストール |
| `update-helper/` | アップデート時に KINO.app を置き換える macOS ヘルパー |
