# Compilar e publicar o KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · **Português** · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Estas etapas são para o desenvolvimento. Para usar o KINO, basta baixá-lo (veja o [README](README.pt.md)).

## Publicar uma versão

Mude `APP_VERSION` em `src/kino_version.py` e faça push na `main`: só isso. Quando a versão da `main` ainda não tem release, o GitHub Actions (`.github/workflows/build.yml`) compila os dois aplicativos, testa, publica a release com a tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) e depois a reinstala num Mac e num Windows limpos com os comandos do README.

Para que o KINO instale sozinho as novas versões, a CI assina as atualizações com o secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): o conteúdo da chave privada criada por `tools/build_update_package.py --init-key`, cuja parte pública é `assets/update_public_key.hex`. Sem esse secret, o KINO anuncia a nova versão e oferece o download.

Este repositório é privado: as versões são publicadas no repositório público [kino-releases](https://github.com/isleepverywellatnight/kino-releases), usado pelos downloads, scripts de instalação e atualizações do KINO. A CI escreve lá com o secret `KINORELEASE`: um token fine-grained limitado ao kino-releases, com Contents de leitura e escrita.

## Compilar na sua máquina

É preciso ter Python 3.12. As bibliotecas do player são copiadas em `vendor/` (ignorado pelo git).

### macOS (Apple Silicon)

A libmpv vem do [IINA](https://iina.io), instalado em `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

O mpv é baixado numa versão fixa, com a soma de verificação conferida; o [7-Zip](https://7-zip.org) faz a extração. O instalador é gerado com o [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Um aplicativo compilado verifica o próprio conteúdo com `--self-check`.

## Testes

A CI roda os dois a cada push. O teste do Windows (workflow «Windows UI») inicia o KINO de verdade, controla-o pela porta DevTools do WebView2 com cliques e arrastos reais, e verifica a janela, o player e os títulos originais.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Traduções

A interface é escrita em francês. `web/js/i18n.js` a traduz na exibição com um dicionário por idioma, `web/js/i18n/<idioma>.js`, que associa cada texto francês à sua tradução (`{}` marca uma parte variável). Um texto ausente aparece em inglês, ou em francês para o inglês.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Para adicionar um idioma: crie `web/js/i18n/<código>.js` no modelo dos outros e acrescente o código em `LANGUAGES` (`web/js/i18n.js`) e `UI_LANGUAGES` (`src/config.py`).

## Conteúdo de `packaging/`

| Arquivo | Função |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Compila o aplicativo com o PyInstaller |
| `make_dmg.sh` | Imagem de disco do macOS |
| `kino.iss` | Instalador do Windows em um clique (Inno Setup) |
| `install.sh`, `install.ps1` | Instalação com um comando, a partir da última release |
| `update-helper/` | Assistente do macOS que substitui o KINO.app durante uma atualização |
