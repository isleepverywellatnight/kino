# Compilar e publicar o KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · **Português** · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Estas etapas são para o desenvolvimento. Para usar o KINO, basta baixá-lo (veja o [README](README.pt.md)).

## Publicar uma versão

Mude `version` em `Cargo.toml` ([workspace.package]) e faça push na `main`: só isso. Quando a versão da `main` ainda não tem release, o GitHub Actions (`.github/workflows/build.yml`) compila os três aplicativos, testa, publica a release com a tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `KINO-Windows-arm64.exe`, `KINO-Linux-x86_64.AppImage`, `KINO-Linux-x86_64.flatpak`, `SHA256SUMS.txt`) e depois a reinstala num Mac e num Windows limpos com os comandos do README.

Para que o KINO instale sozinho as novas versões, a CI assina as atualizações com o secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): o conteúdo da chave privada criada por `cargo run -p kino --bin kino-release -- --init-key`, cuja parte pública é `assets/update_public_key.hex`. Sem esse secret, o KINO anuncia a nova versão e oferece o download.

As versões são publicadas neste repositório; os downloads, scripts de instalação e atualizações do KINO as usam. A CI publica com o próprio `GITHUB_TOKEN`: nenhum outro secret é necessário.

## Compilar na sua máquina

O Rust (a versão de `rust-toolchain.toml`) compila o KINO; o Node.js executa os testes da interface e a ferramenta de tradução. As bibliotecas do player são copiadas em `vendor/` (ignorado pelo git).

### macOS (Apple Silicon)

A libmpv vem do [IINA](https://iina.io), instalado em `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

O mpv é baixado numa versão fixa, com a soma de verificação conferida; o [7-Zip](https://7-zip.org) faz a extração. O instalador é gerado com o [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Para Windows no ARM (`KINO-Windows-arm64.exe`), compile num PC ARM64 e passe `-Arch arm64` aos dois scripts de download e `/DArch=arm64` ao `iscc`.

### Linux (x86_64)

O AppImage é compilado no Ubuntu 22.04, o sistema mais antigo suportado: funciona em sistemas com a mesma glibc ou uma mais recente. Ele traz o WebKitGTK e os plugins do GStreamer com que a página reproduz vídeos; o FFmpeg é o do sistema de compilação.

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy e seus plugins, fixados e verificados, em vendor/
cargo run -p kino-desktop                             # executar a partir do código-fonte
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # autoverificação, inicialização e vídeo, numa tela virtual
```

O Flatpak (`KINO-Linux-x86_64.flatpak`, runtime do GNOME) empacota a mesma compilação: `tools/fetch_ffmpeg.sh` e depois `packaging/build_flatpak.sh` (requer `flatpak`, `flatpak-builder` e o Flathub como remoto de usuário).

Um aplicativo compilado verifica o próprio conteúdo com `--self-check`.

## Testes

A CI roda os dois a cada push. O teste do Windows (workflow «Windows UI») inicia o KINO de verdade, controla-o pela porta DevTools do WebView2 com cliques e arrastos reais, e verifica a janela, o player e os títulos originais.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Traduções

A interface é escrita em francês. Cada texto francês tem uma chave: `web/js/i18n/fr.js` dá o texto francês de cada chave e o dicionário de cada idioma, `web/js/i18n/<idioma>.js`, sua tradução (`{}` marca uma parte variável). `web/js/i18n.js` os usa na exibição. Um texto ausente aparece em inglês, ou em francês para o inglês. `tests/test_i18n_catalogs.js` verifica que cada texto do código tem sua chave e que os dicionários só traduzem chaves existentes.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
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
