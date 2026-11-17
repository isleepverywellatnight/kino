# Compilar e publicar o KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · **Português** · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Estas etapas são para o desenvolvimento. Para usar o KINO, basta baixá-lo (veja o [README](README.pt.md)).

## Publicar uma versão

Mude `version` em `Cargo.toml` ([workspace.package]) e faça push na `main`: só isso. Quando a versão da `main` ainda não tem release, o GitHub Actions (`.github/workflows/build.yml`) compila os dois aplicativos, testa, publica a release com a tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) e depois a reinstala num Mac e num Windows limpos com os comandos do README.

Para que o KINO instale sozinho as novas versões, a CI assina as atualizações com o secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): o conteúdo da chave privada criada por `cargo run -p kino --bin kino-release -- --init-key`, cuja parte pública é `assets/update_public_key.hex`. Sem esse secret, o KINO anuncia a nova versão e oferece o download.

As versões são publicadas neste repositório; os downloads, scripts de instalação e atualizações do KINO as usam. A CI publica com o próprio `GITHUB_TOKEN`: nenhum outro secret é necessário.

## Compilar na sua máquina

O Rust (a versão de `rust-toolchain.toml`) compila o KINO; o Python 3.12 só serve às ferramentas que obtêm mpv e FFmpeg. As bibliotecas do player são copiadas em `vendor/` (ignorado pelo git).

### macOS (Apple Silicon)

A libmpv vem do [IINA](https://iina.io), instalado em `/Applications`.

```bash
python3.12 -m venv .venv && .venv/bin/pip install imageio-ffmpeg
.venv/bin/python tools/vendor_libmpv.py               # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

O mpv é baixado numa versão fixa, com a soma de verificação conferida; o [7-Zip](https://7-zip.org) faz a extração. O instalador é gerado com o [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install imageio-ffmpeg
.venv\Scripts\python tools\fetch_mpv_windows.py
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1 (.venv\Scripts\python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Um aplicativo compilado verifica o próprio conteúdo com `--self-check`.

## Testes

A CI roda os dois a cada push. O teste do Windows (workflow «Windows UI») inicia o KINO de verdade, controla-o pela porta DevTools do WebView2 com cliques e arrastos reais, e verifica a janela, o player e os títulos originais.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Traduções

A interface é escrita em francês. `web/js/i18n.js` a traduz na exibição com um dicionário por idioma, `web/js/i18n/<idioma>.js`, que associa cada texto francês à sua tradução (`{}` marca uma parte variável). Um texto ausente aparece em inglês, ou em francês para o inglês.

```bash
python3 tools/i18n.py extract       # web/ and crates/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Para adicionar um idioma: crie `web/js/i18n/<código>.js` no modelo dos outros e acrescente o código em `LANGUAGES` (`web/js/i18n.js`) e `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Conteúdo de `packaging/`

| Arquivo | Função |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | Imagem de disco do macOS |
| `kino.iss` | Instalador do Windows em um clique (Inno Setup) |
| `install.sh`, `install.ps1` | Instalação com um comando, a partir da última release |
| `update-helper/` | Assistente do macOS que substitui o KINO.app durante uma atualização |
