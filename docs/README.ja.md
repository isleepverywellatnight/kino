# KINO

[English](../README.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Italiano](README.it.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [中文](README.zh.md) · **日本語**

KINO は macOS と Windows 向けのデスクトップアプリです。ご自身の Debrid サービスのアカウント（Real-Debrid、AllDebrid、TorBox、Debrid-Link、Premiumize）を使って、映画・シリーズ・アニメのカタログ、視聴記録、再生をひとつにまとめます。

インターフェースは英語、フランス語、ドイツ語、スペイン語、イタリア語、ポルトガル語、ロシア語、中国語、日本語に対応しています。KINO はコンピュータの言語に従い、設定 → アプリケーション → 表示言語 で変更できます。

## 免責事項

KINO はコンテンツをホスト・提供・配布しません。ユーザーが選んだソースを検索し、ユーザー自身の Debrid サービスが返すものを再生します。視聴する内容と、各国の著作権法の遵守については、各ユーザーが責任を負います。

## KINO でできること

- **カタログ**：映画、シリーズ、アニメ。詳細ページ、キャスト、予告編つきで、各作品は原題で表示されます。
- **Debrid サービスなし**：アドオンを追加（ソースが増え、そのカタログは「探索」に表示）、
  内容を理解して同意した上でトレントを P2P で再生できます（IP アドレスが見え、視聴中は共有します）。
- **マンガ**：カタログ全体と連載中のシリーズ。各話を複数のサイトから集め（あなたの言語があればその言語、なければ英語）、
  トレントで完全な HD の巻を入手でき、自分の CBZ ファイル・画像フォルダも読めます。1 ページずつでも縦スクロールでも、
  日本式の向きでも読め、KINO が読んだ位置を覚えます。
- **再生**：mpv ベースの内蔵プレーヤー（HDR、字幕、音声トラック、オープニングのスキップ）。
- **記録**：マイリスト、視聴中、視聴済み、マイシリーズ、公開カレンダー、ワンタップ評価。
- **おすすめ**：「あなたへのおすすめ」と「気に入ったなら」を、視聴・評価した作品からお使いのコンピュータ上で計算します。
- **アニメ**：AniList の行、週間放送スケジュール、公開順のシリーズ視聴順。
- **他のサービス**：Letterboxd のインポート、Trakt、Discord のステータス表示。
- **みんなで、どこでも**：Watch Party、ローカルネットワーク上のテレビでの再生、スマートフォンをリモコンに。
- **オフライン**：映画やシーズンをダウンロードして、インターネットなしで視聴。
- **データ**：お使いのコンピュータに保存。毎日バックアップし、Google Drive での同期も選べます。

## KINO をダウンロード

| システム | ダウンロード |
|---|---|
| **macOS** — Apple シリコン搭載 Mac（M1 以降）、macOS 14 以降 | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 または 11、64 ビット | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |

KINO には必要なものがすべて入っています：ビデオプレーヤー、FFmpeg、インターフェース。ほかに何もインストールする必要はありません。あとは設定で Debrid サービスのキーを貼り付けるだけです。

- **macOS**：`.dmg` ファイルを開き、KINO を「アプリケーション」にドラッグします。
- **Windows**：`KINO-Windows.exe` を実行します。KINO は管理者権限なしでワンクリックでインストールされ、そのまま起動します。

以降のバージョンは KINO からインストールできます：**設定 → アップデート**。

### コマンドひとつでインストール

このコマンドは最新版をダウンロードし、ファイルを検証して、KINO をインストールして起動します。アップデートにも使えます。

macOS（ターミナル）：

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### 初回起動がブロックされた場合

KINO はまだ Apple や Microsoft の署名を受けていません。ブラウザからダウンロードした場合：

- **macOS**：KINO が開かないときは、システム設定 → プライバシーとセキュリティ に進み、**このまま開く** をクリックします。
- **Windows**：「Windows によって PC が保護されました」と表示されたら、**詳細情報** をクリックし、**実行** をクリックします。

上のコマンドを使えば、どちらのメッセージも表示されません。

## データの保存場所

- `~/.kino_data.db`：ライブラリ、履歴、評価、キャッシュ。
- `~/.rd_cinehub_config.json`：設定と各サービスのキー。
- `~/Library/Application Support/KINO`（macOS）または `%APPDATA%\KINO`（Windows）：バックアップ、エラーログ。Windows では、インストール中のアップデートは `%LOCALAPPDATA%\KINO\Updates` にあります。

キーはお使いのコンピュータにだけ保存され、バックアップも同期もされません。KINO をアンインストールしてもこれらのデータは削除されません。

## リポジトリの構成

| フォルダ | 内容 |
|---|---|
| `src/` | KINO の Python コード。`desktop.py` で起動 |
| `web/` | インターフェース（HTML、CSS、JavaScript） |
| `addons/` | 追加ソース（設定で有効化） |
| `assets/` | アプリに同梱するアイコンとファイル |
| `packaging/` | アプリのビルド、インストーラー、インストールスクリプト |
| `tools/` | ビルドと公開のツール |
| `tests/` | テスト |
| `docs/` | 各 README の他言語版 |

KINO のビルドやバージョンの公開については [packaging/README.md](../packaging/README.md) を参照してください。

## ライセンス

KINO は MIT ライセンスで公開されています（[LICENSE](../LICENSE) を参照）。`web/js/vendor` フォルダには PeerJS と QRCode.js が含まれ、それぞれ独自の MIT ライセンスです。アプリに同梱される mpv、libmpv、FFmpeg は GPL または LGPL ライセンスです。
