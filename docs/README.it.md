# KINO

[English](../README.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Español](README.es.md) · **Italiano** · [Português](README.pt.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [日本語](README.ja.md)

KINO è un'app desktop per macOS e Windows che riunisce in un unico posto un catalogo di film, serie e anime, il monitoraggio di ciò che guardi e la riproduzione, tramite il tuo account presso un servizio debrid (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

L'interfaccia è disponibile in inglese, francese, tedesco, spagnolo, italiano, portoghese, russo, cinese e giapponese: KINO segue la lingua del tuo computer, e puoi cambiarla in Impostazioni → Applicazione → Lingua dell'interfaccia.

## Avvertenza

KINO non ospita, fornisce né distribuisce alcun contenuto. Interroga le fonti scelte dall'utente e riproduce ciò che restituisce il suo servizio debrid. Ogni utente è l'unico responsabile di ciò che guarda e del rispetto del diritto d'autore nel proprio paese.

## Cosa fa KINO

- **Catalogo**: film, serie e anime, con schede, cast, trailer e ogni titolo con il suo nome originale.
- **Senza servizio di debrid**: aggiungi add-on (più fonti e i loro
  cataloghi in Esplora) e guarda i torrent in P2P dopo aver accettato ciò che
  comporta (il tuo indirizzo IP è visibile e condividi mentre guardi).
- **Manga**: leggi le serie di MangaDex (tradotte da gruppi di volontari) e i tuoi
  file CBZ o cartelle di immagini, pagina per pagina o a scorrimento, in senso
  giapponese se vuoi; KINO ricorda dove ti sei fermato.
- **Riproduzione**: lettore video integrato basato su mpv (HDR, sottotitoli, tracce audio, salto della sigla).
- **Monitoraggio**: La mia lista, in corso, già visti, le mie serie, calendario delle uscite, voti con un gesto.
- **Suggerimenti**: «Per te» e «Se ti è piaciuto», calcolati sul tuo computer in base a ciò che hai visto e votato.
- **Anime**: righe da AniList, palinsesto settimanale, percorso di una saga in ordine di uscita.
- **Altri servizi**: importazione da Letterboxd, Trakt, stato su Discord.
- **Insieme e altrove**: Watch Party, riproduzione su una TV della rete locale, telecomando dal telefono.
- **Offline**: scarica un film o una stagione per guardarli senza internet.
- **I tuoi dati**: conservati sul tuo computer, con un backup giornaliero e sincronizzazione facoltativa tramite Google Drive.

## Scarica KINO

| Sistema | Download |
|---|---|
| **macOS** — Mac con Apple Silicon (M1 o successivo), macOS 14 o successivo | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 o 11, 64 bit | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/KINO-Windows.exe) |

KINO contiene tutto ciò che gli serve: lettore video, FFmpeg, interfaccia. Non c'è nient'altro da installare. Poi incolla la chiave del tuo servizio debrid in Impostazioni.

- **macOS**: apri il file `.dmg` e trascina KINO in Applicazioni.
- **Windows**: avvia `KINO-Windows.exe`. KINO si installa con un clic, senza diritti di amministratore, poi si apre.

Le versioni successive si installano da KINO stesso: **Impostazioni → Aggiornamenti**.

### Installare con un solo comando

Questo comando scarica l'ultima versione, verifica il file, installa KINO e lo apre. Serve anche per aggiornarlo.

macOS (app Terminale):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/install.ps1 | iex
```

### Se il sistema blocca il primo avvio

KINO non è ancora firmato da Apple né da Microsoft. Dopo un download dal browser:

- **macOS**: se KINO non si apre, vai in Impostazioni di Sistema → Privacy e sicurezza, poi fai clic su **Apri comunque**.
- **Windows**: se compare «Windows ha protetto il PC», fai clic su **Ulteriori informazioni**, poi su **Esegui comunque**.

I comandi qui sopra evitano entrambi i messaggi.

## Dove sono i tuoi dati

- `~/.kino_data.db`: libreria, cronologia, voti e cache.
- `~/.rd_cinehub_config.json`: impostazioni e chiavi dei servizi.
- `~/Library/Application Support/KINO` (macOS) o `%APPDATA%\KINO` (Windows): backup, registro degli errori. Gli aggiornamenti in corso sono in `%LOCALAPPDATA%\KINO\Updates` su Windows.

Le chiavi restano sul tuo computer: non vengono né salvate nei backup né sincronizzate. Disinstallare KINO non elimina questi dati.

## Struttura del repository

| Cartella | Contenuto |
|---|---|
| `src/` | Codice Python di KINO; `desktop.py` lo avvia |
| `web/` | Interfaccia (HTML, CSS, JavaScript) |
| `addons/` | Fonti aggiuntive, attivabili in Impostazioni |
| `assets/` | Icone e file forniti con l'app |
| `packaging/` | Build delle app, installer, script di installazione |
| `tools/` | Strumenti di build e pubblicazione |
| `tests/` | Test |
| `docs/` | I README in altre lingue |

Per compilare KINO o pubblicare una versione, vedi [packaging/README.md](../packaging/README.md).

## Licenza

KINO è distribuito con licenza MIT (vedi [LICENSE](../LICENSE)). La cartella `web/js/vendor` contiene PeerJS e QRCode.js, ciascuno con la propria licenza MIT. mpv, libmpv e FFmpeg, inclusi nelle app, sono con licenza GPL o LGPL.
