# 构建和发布 KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · **中文** · [日本語](packaging.ja.md)

以下步骤用于开发。若只是使用 KINO，直接下载即可（见 [README](README.zh.md)）。

## 发布新版本

修改 `src/kino_version.py` 中的 `APP_VERSION` 并推送到 `main`，就这么简单。当 `main` 上的版本还没有发布时，GitHub Actions（`.github/workflows/build.yml`）会构建两个应用、进行测试、发布带 `vX.Y.Z` 标签的版本（`KINO-macOS.dmg`、`KINO-Windows.exe`、`SHA256SUMS.txt`），然后用 README 中的命令在全新的 Mac 和 Windows 上重新安装验证。

要让 KINO 自动安装新版本，CI 会用 `KINO_UPDATE_SIGNING_KEY` 机密（Settings → Secrets and variables → Actions）为更新签名：其内容是 `tools/build_update_package.py --init-key` 生成的私钥，对应的公钥是 `assets/update_public_key.hex`。没有这个机密时，KINO 只会提示有新版本并提供下载。

本仓库为私有：版本发布在公开仓库 [kino-releases](https://github.com/isleepverywellatnight/kino-releases)，KINO 的下载、安装脚本和更新都使用它。CI 通过 `KINORELEASE` 机密写入：一个仅限 kino-releases、Contents 读写权限的细粒度令牌。

## 在本机构建

需要 Python 3.12。播放器所需的库会复制到 `vendor/`（git 忽略）。

### macOS (Apple Silicon)

libmpv 取自安装在 `/Applications` 中的 [IINA](https://iina.io)。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv 以固定版本下载并校验其哈希；由 [7-Zip](https://7-zip.org) 解压。安装程序使用 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 构建。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

构建好的应用可用 `--self-check` 检查自身内容。

## 测试

CI 在每次推送时运行这两项测试。Windows 测试（工作流“Windows UI”）会启动真实的 KINO，通过 WebView2 的 DevTools 端口用真实的点击和拖动来操作，并检查窗口、播放器和原始标题。

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## 翻译

界面原文为法语。`web/js/i18n.js` 在显示时进行翻译，每种语言一个词典 `web/js/i18n/<语言>.js`，将每段法语文本对应到译文（`{}` 表示可变部分）。缺失的文本显示为英语，英语缺失时显示法语。

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

添加一种语言：仿照其他文件创建 `web/js/i18n/<代码>.js`，然后把代码加入 `LANGUAGES`（`web/js/i18n.js`）和 `UI_LANGUAGES`（`src/config.py`）。

## `packaging/` 中的文件

| 文件 | 用途 |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | 用 PyInstaller 构建应用 |
| `make_dmg.sh` | macOS 磁盘映像 |
| `kino.iss` | Windows 一键安装程序（Inno Setup） |
| `install.sh`, `install.ps1` | 从最新版本一条命令安装 |
| `update-helper/` | 更新时替换 KINO.app 的 macOS 辅助程序 |
