# KINO

[English](../README.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Italiano](README.it.md) · [Português](README.pt.md) · [Русский](README.ru.md) · **中文** · [日本語](README.ja.md)

KINO 是一款适用于 macOS 和 Windows 的桌面应用，通过你自己的 Debrid 服务账户（Real-Debrid、AllDebrid、TorBox、Debrid-Link、Premiumize），把电影、剧集和动画目录、观看记录与播放集中在一处。

界面提供英语、法语、德语、西班牙语、意大利语、葡萄牙语、俄语、中文和日语：KINO 跟随电脑的语言，也可以在 设置 → 应用 → 界面语言 中更改。

## 免责声明

KINO 不托管、不提供也不分发任何内容。它查询用户自选的来源，并播放用户自己的 Debrid 服务返回的内容。每位用户需自行为观看的内容负责，并遵守所在国家的版权法律。

## KINO 能做什么

- **目录**：电影、剧集和动画，含详情页、演员、预告片，每个作品都显示原始标题。
- **没有 Debrid 服务**：添加插件（更多来源，以及“探索”中的目录），
  并在了解其含义后用 P2P 播放种子（你的 IP 地址可见，观看时你也在分享）。
- **漫画**：完整目录和正在连载的作品，每一话从多个网站汇总（有你的语言时用你的语言，否则用英文），
  通过种子获取完整高清单行本，还有你自己的 CBZ 文件或图片文件夹；可逐页或长条滚动阅读，也可按日式方向翻页。
  KINO 会记住你读到哪里。
- **播放**：基于 mpv 的内置播放器（HDR、字幕、音轨、跳过片头）。
- **追踪**：我的片单、正在观看、已看、我的剧集、上映日历、一键评分。
- **推荐**：“为你推荐”和“如果你喜欢”，根据你看过和评过的内容在你的电脑上计算。
- **动画**：来自 AniList 的栏目、每周播出表、按上映顺序排列的系列观看顺序。
- **其他服务**：导入 Letterboxd、Trakt、Discord 状态。
- **一起看，随处看**：Watch Party、在局域网电视上播放、用手机当遥控器。
- **离线**：下载电影或整季，无网络也能观看。
- **你的数据**：保存在你的电脑上，每天自动备份，可选通过 Google Drive 同步。

## 下载 KINO

| 系统 | 下载 |
|---|---|
| **macOS** — Apple 芯片 Mac（M1 或更新），macOS 14 或更新 | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 或 11，64 位 | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |

KINO 自带所需的一切：视频播放器、FFmpeg、界面。无需另外安装任何东西。之后在设置中粘贴你的 Debrid 服务密钥即可。

- **macOS**：打开 `.dmg` 文件，把 KINO 拖到“应用程序”。
- **Windows**：运行 `KINO-Windows.exe`。KINO 一键安装，无需管理员权限，安装后自动打开。

之后的版本可在 KINO 中直接安装：**设置 → 更新**。

### 一条命令安装

这条命令会下载最新版本、校验文件、安装 KINO 并打开它。它也可用于更新。

macOS（“终端”应用）：

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### 如果系统阻止首次启动

KINO 尚未经过 Apple 或 Microsoft 签名。通过浏览器下载后：

- **macOS**：如果 KINO 无法打开，请前往 系统设置 → 隐私与安全性，然后点击 **仍要打开**。
- **Windows**：如果出现“Windows 已保护你的电脑”，请点击 **更多信息**，然后点击 **仍要运行**。

使用上面的命令不会出现这两条提示。

## 校验下载文件

每个版本的文件都由 GitHub Actions 从该版本所指的提交构建。它的 SHA-256 写在
`SHA256SUMS.txt` 中；从 1.9.0 之后的版本起，构建证明会说明是哪个工作流、哪个提交生成了它：

```bash
gh attestation verify KINO-macOS.dmg -R isleepverywellatnight/kino
```

## 隐私

KINO 没有自己的服务器，不收集任何信息。它直接与以下对象通信：你连接的服务（你的 Debrid
服务、Trakt、Discord、Google Drive）、它读取的公共目录（如 Cinemeta、AniList、MangaDex，
以及使用你的密钥的 TMDB）、它搜索的来源（索引器、你的插件、漫画网站）、开启 P2P 播放时的
BitTorrent 节点，以及 GitHub（在启动时和每六小时检查一次新版本）。设置 → 更新 可以关闭此检查。

## 数据存放位置

- `~/.kino_data.db`：媒体库、历史、评分和缓存。
- `~/.rd_cinehub_config.json`：设置和各服务的密钥。
- `~/Library/Application Support/KINO`（macOS）或 `%APPDATA%\KINO`（Windows）：备份、错误日志。Windows 上进行中的更新位于 `%LOCALAPPDATA%\KINO\Updates`。

密钥只保存在你的电脑上：不会被备份，也不会被同步。卸载 KINO 不会删除这些数据。

## 仓库结构

| 文件夹 | 内容 |
|---|---|
| `crates/` | KINO 的 Rust 代码：`kino`（服务器）和 `kino-desktop`（应用） |
| `web/` | 界面（HTML、CSS、JavaScript） |
| `assets/` | 随应用附带的图标和文件 |
| `packaging/` | 应用构建、安装程序、安装脚本 |
| `tools/` | 构建与发布工具 |
| `tests/` | 测试 |
| `docs/` | 各 README 的其他语言版本 |

构建 KINO 或发布新版本：请参阅 [packaging/README.md](../packaging/README.md)。

## 许可证

KINO 以 MIT 许可证发布（见 [LICENSE](../LICENSE)）。`web/js/vendor` 文件夹包含 PeerJS 和 QRCode.js，各自采用 MIT 许可证。应用内附带的 mpv、libmpv 和 FFmpeg 采用 GPL 或 LGPL 许可证。
