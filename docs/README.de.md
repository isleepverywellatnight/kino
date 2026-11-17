# KINO

[English](../README.md) · [Français](README.fr.md) · **Deutsch** · [Español](README.es.md) · [Italiano](README.it.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [日本語](README.ja.md)

KINO ist eine Desktop-App für macOS und Windows, die einen Katalog von Filmen, Serien und Anime, das Verfolgen dessen, was du schaust, und die Wiedergabe an einem Ort vereint – über dein eigenes Konto bei einem Debrid-Dienst (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

Die Oberfläche gibt es auf Englisch, Französisch, Deutsch, Spanisch, Italienisch, Portugiesisch, Russisch, Chinesisch und Japanisch: KINO folgt der Sprache deines Computers, und du kannst sie unter Einstellungen → Anwendung → Sprache der Oberfläche ändern.

## Hinweis

KINO hostet, liefert und verbreitet keine Inhalte. Es fragt Quellen ab, die der Nutzer wählt, und spielt ab, was der eigene Debrid-Dienst des Nutzers liefert. Jeder Nutzer ist allein verantwortlich für das, was er schaut, und für die Einhaltung des Urheberrechts in seinem Land.

## Was KINO kann

- **Katalog**: Filme, Serien und Anime mit Details, Besetzung, Trailern und jedem Titel unter seinem Originalnamen.
- **Ohne Debrid-Dienst**: Add-ons hinzufügen (mehr Quellen und ihre
  Kataloge unter Entdecken) und Torrents per P2P abspielen, nachdem du zugestimmt
  hast, was das bedeutet (deine IP-Adresse ist sichtbar, du teilst beim Schauen).
- **Manga**: der ganze Katalog und die laufenden Serien, jedes Kapitel aus
  mehreren Seiten zusammengeführt (in deiner Sprache, wenn es sie gibt, sonst
  auf Englisch), komplette HD-Bände per Torrent und deine eigenen CBZ-Dateien
  und Bildordner; Seite für Seite oder als langes Scrollen, auf Wunsch in
  japanischer Leserichtung. KINO merkt sich, wo du aufgehört hast.
- **Wiedergabe**: integrierter Videoplayer auf Basis von mpv (HDR, Untertitel, Tonspuren, Intro überspringen).
- **Verfolgen**: Meine Liste, laufende Titel, Gesehen, Meine Serien, Veröffentlichungskalender, Bewertung mit einem Klick.
- **Vorschläge**: „Für dich“ und „Wenn dir gefallen hat“, auf deinem Computer berechnet aus dem, was du gesehen und bewertet hast.
- **Anime**: Reihen aus AniList, Wochenplan, Verlauf einer Saga in Erscheinungsreihenfolge.
- **Andere Dienste**: Letterboxd-Import, Trakt, Discord-Status.
- **Gemeinsam und anderswo**: Watch Party, Wiedergabe auf einem Fernseher im lokalen Netzwerk, Fernbedienung per Telefon.
- **Offline**: einen Film oder eine Staffel herunterladen, um ohne Internet zu schauen.
- **Deine Daten**: auf deinem Computer gespeichert, mit täglicher Sicherung und optionaler Synchronisierung über Google Drive.

## KINO herunterladen

| System | Download |
|---|---|
| **macOS** — Mac mit Apple Silicon (M1 oder neuer), macOS 14 oder neuer | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 oder 11, 64 Bit | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |

KINO enthält alles, was es braucht: Videoplayer, FFmpeg, Oberfläche. Es muss nichts weiter installiert werden. Danach fügst du den Schlüssel deines Debrid-Dienstes in den Einstellungen ein.

- **macOS**: öffne die `.dmg`-Datei und ziehe KINO in Programme.
- **Windows**: starte `KINO-Windows.exe`. KINO installiert sich mit einem Klick, ohne Administratorrechte, und öffnet sich dann.

Neuere Versionen installierst du direkt in KINO: **Einstellungen → Updates**.

### Mit einem einzigen Befehl installieren

Dieser Befehl lädt die neueste Version herunter, prüft die Datei, installiert KINO und öffnet es. Er dient auch zum Aktualisieren.

macOS (Terminal-App):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### Wenn das System den ersten Start blockiert

KINO ist noch nicht von Apple oder Microsoft signiert. Nach einem Download über den Browser:

- **macOS**: wenn KINO sich nicht öffnen lässt, gehe zu Systemeinstellungen → Datenschutz & Sicherheit und klicke auf **Dennoch öffnen**.
- **Windows**: wenn „Der Computer wurde durch Windows geschützt“ erscheint, klicke auf **Weitere Informationen** und dann auf **Trotzdem ausführen**.

Die Befehle oben vermeiden beide Meldungen.

## Wo deine Daten liegen

- `~/.kino_data.db`: Bibliothek, Verlauf, Bewertungen und Cache.
- `~/.rd_cinehub_config.json`: Einstellungen und Schlüssel der Dienste.
- `~/Library/Application Support/KINO` (macOS) oder `%APPDATA%\KINO` (Windows): Sicherungen, Fehlerprotokoll. Laufende Updates liegen unter Windows in `%LOCALAPPDATA%\KINO\Updates`.

Die Schlüssel bleiben auf deinem Computer: Sie werden weder gesichert noch synchronisiert. Eine Deinstallation von KINO löscht diese Daten nicht.

## Aufbau des Repositorys

| Ordner | Inhalt |
|---|---|
| `crates/` | KINO-Code in Rust: `kino` (der Server) und `kino-desktop` (die App) |
| `web/` | Oberfläche (HTML, CSS, JavaScript) |
| `assets/` | Icons und Dateien, die mit der App ausgeliefert werden |
| `packaging/` | App-Builds, Installer, Installationsskripte |
| `tools/` | Build- und Veröffentlichungswerkzeuge |
| `tests/` | Tests |
| `docs/` | Die READMEs in anderen Sprachen |

KINO bauen oder eine Version veröffentlichen: siehe [packaging/README.md](../packaging/README.md).

## Lizenz

KINO steht unter der MIT-Lizenz (siehe [LICENSE](../LICENSE)). Der Ordner `web/js/vendor` enthält PeerJS und QRCode.js, jeweils unter eigener MIT-Lizenz. mpv, libmpv und FFmpeg, die in den Apps enthalten sind, stehen unter GPL oder LGPL.
