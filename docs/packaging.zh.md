# 构建和发布 KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · **中文** · [日本語](packaging.ja.md)

以下步骤用于开发。若只是使用 KINO，直接下载即可（见 [README](README.zh.md)）。

## 发布新版本

修改 `Cargo.toml` ([workspace.package]) 中的 `version` 并推送到 `main`，就这么简单。当 `main` 上的版本还没有发布时，GitHub Actions（`.github/workflows/build.yml`）会构建三个应用、进行测试、发布带 `vX.Y.Z` 标签的版本（`KINO-macOS.dmg`、`KINO-Windows.exe`、`KINO-Linux-x86_64.AppImage`、`SHA256SUMS.txt`），然后用 README 中的命令在全新的 Mac 和 Windows 上重新安装验证。

要让 KINO 自动安装新版本，CI 会用 `KINO_UPDATE_SIGNING_KEY` 机密（Settings → Secrets and variables → Actions）为更新签名：其内容是 `cargo run -p kino --bin kino-release -- --init-key` 生成的私钥，对应的公钥是 `assets/update_public_key.hex`。没有这个机密时，KINO 只会提示有新版本并提供下载。

版本发布在本仓库中，KINO 的下载、安装脚本和更新都使用它们。CI 使用自身的 `GITHUB_TOKEN` 发布，无需其他机密。

## 在本机构建

KINO 由 Rust（`rust-toolchain.toml` 中的版本）构建；界面测试和翻译工具由 Node.js 运行。播放器所需的库会复制到 `vendor/`（git 忽略）。

### macOS (Apple Silicon)

libmpv 取自安装在 `/Applications` 中的 [IINA](https://iina.io)。

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv 以固定版本下载并校验其哈希；由 [7-Zip](https://7-zip.org) 解压。安装程序使用 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 构建。

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

### Linux (x86_64)

AppImage 在 Ubuntu 22.04（支持的最旧系统）上构建：可在 glibc 相同或更新的系统上运行。它包含 WebKitGTK 以及页面播放视频所用的 GStreamer 插件；FFmpeg 取自构建系统。

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy 及其插件（固定版本并校验）放入 vendor/
cargo run -p kino-desktop                             # 从源码运行
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # 在虚拟屏幕上自检、启动并播放视频
```

构建好的应用可用 `--self-check` 检查自身内容。

## 测试

CI 在每次推送时运行这两项测试。Windows 测试（工作流“Windows UI”）会启动真实的 KINO，通过 WebView2 的 DevTools 端口用真实的点击和拖动来操作，并检查窗口、播放器和原始标题。

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## 翻译

界面原文为法语。每段法语文本都有一个键：`web/js/i18n/fr.js` 给出每个键的法语文本，各语言的词典 `web/js/i18n/<语言>.js` 给出其译文（`{}` 表示可变部分）。`web/js/i18n.js` 在显示时使用它们。缺失的文本显示为英语，英语缺失时显示法语。`tests/test_i18n_catalogs.js` 检查代码中的每段文本都有键，且词典只翻译已存在的键。

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

添加一种语言：仿照其他文件创建 `web/js/i18n/<代码>.js`，然后把代码加入 `LANGUAGES`（`web/js/i18n.js`）和 `UI_LANGUAGES`（`crates/kino/src/config.rs`）。

## `packaging/` 中的文件

| 文件 | 用途 |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | macOS 磁盘映像 |
| `kino.iss` | Windows 一键安装程序（Inno Setup） |
| `install.sh`, `install.ps1` | 从最新版本一条命令安装 |
| `update-helper/` | 更新时替换 KINO.app 的 macOS 辅助程序 |
