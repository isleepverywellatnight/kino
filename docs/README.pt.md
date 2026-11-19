# KINO

[English](../README.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Italiano](README.it.md) · **Português** · [Русский](README.ru.md) · [中文](README.zh.md) · [日本語](README.ja.md)

KINO é um aplicativo para macOS e Windows que reúne num só lugar um catálogo de filmes, séries e animes, o acompanhamento do que você assiste e a reprodução, pela sua própria conta num serviço de debrid (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

A interface está disponível em inglês, francês, alemão, espanhol, italiano, português, russo, chinês e japonês: o KINO segue o idioma do seu computador, e você pode mudá-lo em Configurações → Aplicativo → Idioma da interface.

## Aviso

O KINO não hospeda, fornece nem distribui nenhum conteúdo. Ele consulta as fontes que o usuário escolhe e reproduz o que o serviço de debrid do próprio usuário entrega. Cada usuário é o único responsável pelo que assiste e pelo respeito aos direitos autorais no seu país.

## O que o KINO faz

- **Catálogo**: filmes, séries e animes, com páginas, elenco, trailers e cada título com seu nome original.
- **Sem serviço de debrid**: adicione complementos (mais fontes e
  seus catálogos em Explorar) e reproduza torrents por P2P depois de aceitar o
  que isso implica (seu endereço IP fica visível e você compartilha enquanto assiste).
- **Mangá**: todo o catálogo e as séries em publicação, cada capítulo reunido
  de vários sites (no seu idioma quando existe, senão em inglês), volumes
  completos em HD por torrent e seus próprios arquivos CBZ e pastas de imagens;
  leitura página por página ou em rolagem, no sentido japonês se quiser. O KINO
  lembra onde você parou.
- **Reprodução**: player de vídeo integrado baseado no mpv (HDR, legendas, faixas de áudio, pular abertura).
- **Acompanhamento**: Minha lista, em andamento, assistidos, minhas séries, calendário de lançamentos, notas com um toque.
- **Sugestões**: «Para você» e «Se você gostou», calculadas no seu computador a partir do que você assistiu e avaliou.
- **Anime**: fileiras do AniList, programação da semana, percurso de uma saga em ordem de lançamento.
- **Outros serviços**: importação do Letterboxd, Trakt, presença no Discord.
- **Juntos e em outro lugar**: Watch Party, reprodução numa TV da rede local, controle remoto pelo celular.
- **Offline**: baixe um filme ou uma temporada para assistir sem internet.
- **Seus dados**: guardados no seu computador, com um backup diário e sincronização opcional pelo Google Drive.

## Baixar o KINO

| Sistema | Baixar |
|---|---|
| **macOS** — Mac com Apple Silicon (M1 ou mais recente), macOS 14 ou mais recente | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 ou 11, 64 bits | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |
| **Windows no ARM** — Windows 11 num PC ARM (Snapdragon…) | [**KINO-Windows-arm64.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows-arm64.exe) |
| **Linux** — x86_64: Ubuntu 22.04, Debian 12, Fedora 36 ou mais recente | [**KINO-Linux-x86_64.AppImage**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.AppImage) |

O KINO contém tudo de que precisa: player de vídeo, FFmpeg, interface. Não há mais nada para instalar. Depois, cole a chave do seu serviço de debrid em Configurações.

- **macOS**: abra o arquivo `.dmg` e arraste o KINO para Aplicativos.
- **Windows**: execute `KINO-Windows.exe`. O KINO se instala com um clique, sem direitos de administrador, e abre em seguida.
- **Linux**: torne `KINO-Linux-x86_64.AppImage` executável (Propriedades → Permissões, ou `chmod +x`) e abra-o. Os vídeos tocam na janela do KINO, ou no mpv se estiver instalado. As próximas versões são instaladas pelo próprio KINO, como nos outros sistemas.

As próximas versões são instaladas pelo próprio KINO: **Configurações → Atualizações**.

### Instalar com um único comando

Este comando baixa a versão mais recente, verifica o arquivo, instala o KINO e o abre. Também serve para atualizá-lo.

macOS (app Terminal):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### Se o sistema bloquear a primeira abertura

O KINO ainda não é assinado pela Apple nem pela Microsoft. Depois de um download pelo navegador:

- **macOS**: se o KINO não abrir, vá em Ajustes do Sistema → Privacidade e Segurança e clique em **Abrir Mesmo Assim**.
- **Windows**: se aparecer «O Windows protegeu o computador», clique em **Mais informações** e depois em **Executar assim mesmo**.

Os comandos acima evitam as duas mensagens.

## Verificar um download

Cada arquivo de uma versão é construído pelo GitHub Actions a partir do commit
que a versão indica. O SHA-256 dele está em `SHA256SUMS.txt` e, a partir da
versão seguinte à 1.9.0, uma atestação de build diz qual workflow e qual commit
o produziram:

```bash
gh attestation verify KINO-macOS.dmg -R isleepverywellatnight/kino
```

## Privacidade

O KINO não tem servidor próprio e não coleta nada. Ele fala diretamente com os
serviços que você conecta (seu serviço de debrid, Trakt, Discord, Google Drive),
com os catálogos públicos que lê (como Cinemeta, AniList, MangaDex e TMDB com
sua chave), com as fontes que consulta (indexadores, seus complementos, sites de
mangá), com os pares BitTorrent quando a reprodução P2P está ligada, e com o
GitHub para procurar uma nova versão ao iniciar e a cada seis horas.
Configurações → Atualizações desliga essa busca.

## Onde ficam seus dados

- `~/.kino_data.db`: biblioteca, histórico, notas e cache.
- `~/.rd_cinehub_config.json`: configurações e chaves dos serviços.
- `~/Library/Application Support/KINO` (macOS) ou `%APPDATA%\KINO` (Windows): backups, registro de erros. As atualizações em andamento ficam em `%LOCALAPPDATA%\KINO\Updates` no Windows.

As chaves ficam no seu computador: não entram em backups nem são sincronizadas. Desinstalar o KINO não apaga esses dados.

## Organização do repositório

| Pasta | Conteúdo |
|---|---|
| `crates/` | Código do KINO em Rust: `kino` (o servidor) e `kino-desktop` (o aplicativo) |
| `web/` | Interface (HTML, CSS, JavaScript) |
| `assets/` | Ícones e arquivos incluídos no aplicativo |
| `packaging/` | Build dos aplicativos, instaladores, scripts de instalação |
| `tools/` | Ferramentas de build e publicação |
| `tests/` | Testes |
| `docs/` | Os READMEs em outros idiomas |

Para compilar o KINO ou publicar uma versão, veja [packaging/README.md](../packaging/README.md).

## Licença

O KINO é publicado sob a licença MIT (veja [LICENSE](../LICENSE)). A pasta `web/js/vendor` contém PeerJS e QRCode.js, cada um com sua própria licença MIT. mpv, libmpv e FFmpeg, incluídos nos aplicativos, estão sob licença GPL ou LGPL.
