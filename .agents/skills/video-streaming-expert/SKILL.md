---
name: video-streaming-expert
description: Specialized guidance for desktop multimedia players, video streaming protocols (HLS/DASH/Direct HTTP range requests), MPV IPC socket control, WebVTT subtitle conversions, and audio track switching.
---

# Video Streaming & Multimedia Expert

Guide de référence pour optimiser le streaming vidéo haute fidélité (4K HDR, Dolby Atmos, HLS/DASH, MPV IPC, gestion des sous-titres et pistes audio) dans les applications multimédia de bureau.

---

## 🎬 1. Streaming HTTP & Requêtes de plage (`Range Requests`)

Les lecteurs vidéo HTML5 et externes dépendent du support des en-têtes HTTP `Range` pour le seeking et la mise en mémoire tampon fluide :
- **En-têtes essentiels à relayer** :
  - Client → Serveur : `Range: bytes=start-end`
  - Serveur → Client : `HTTP/1.1 206 Partial Content`
  - `Content-Range: bytes start-end/total`
  - `Accept-Ranges: bytes`
  - `Content-Length: (end - start + 1)`
- **Buffer & Chunking** :
  - Transférer les données en blocs de 64 Ko à 256 Ko pour limiter l'utilisation CPU et éviter la saturation de la RAM lors de fichiers 4K de 50+ Go.

---

## 💬 2. Conversion & Synchronisation des Sous-titres

### A. Format WebVTT standard
Pour le lecteur HTML5 intégré, tout sous-titre (`.srt`, `.ass`, `.vtt`) doit être servi en encodage `UTF-8` au format WebVTT :
```vtt
WEBVTT

00:01:20.000 --> 00:01:23.500
Texte du sous-titre en français.
```

### B. Conversion de `.srt` vers `.vtt`
- Remplacer les virgules de millisecondes (`00:01:20,500`) par des points (`00:01:20.500`).
- Nettoyer les balises de style non compatibles HTML5 (`{\an8}`, polices personnalisées exotiques).
- Détecter et convertir l'encodage source (ex: `ISO-8859-1` ou `Windows-1252` vers `UTF-8` avec gestion des caractères accentués français `é, è, ê, à, ç, œ`).

---

## 🎛️ 3. Contrôle Avancé de MPV via Socket IPC

Lorsque KINO délègue la lecture à MPV (pour les flux 4K HDR10+ / Dolby Vision sans perte de qualité), le contrôle s'effectue via son socket IPC JSON :

### Spécificités par plateforme :
- **Windows** : Named Pipe Win32 (ex: `\\.\pipe\kino_mpv_socket`)
- **macOS / Linux** : Unix Domain Socket (ex: `/tmp/kino_mpv.sock`)

### Lancement de MPV :
```bash
# Windows
mpv.exe --input-ipc-server=\\.\pipe\kino_mpv_socket --force-window --title="KINO" "URL_STREAM"

# macOS
mpv --input-ipc-server=/tmp/kino_mpv.sock --force-window --title="KINO" "URL_STREAM"
```

### Commandes IPC JSON courantes :
```json
{"command": ["get_property", "time-pos"]}
{"command": ["set_property", "pause", true]}
{"command": ["seek", 10, "relative"]}
{"command": ["cycle", "sub"]}
{"command": ["cycle", "audio"]}
```

---

## 🚀 4. Performance & Économie de Bande Passante
1. **Préchargement intelligent (Preload)** :
   - Ne pas précharger l'intégralité du fichier vidéo (`preload="metadata"` par défaut) pour éviter de consommer inutilement le quota débrideur.
2. **Nettoyage automatique du cache** :
   - Éviter d'accumuler des dizaines de gigaoctets de fragments vidéo dans les répertoires temporaires système.
3. **Détection du codec & Fallback** :
   - WebView2 / WebKit prend en charge nativement H.264/AAC et VP9/AV1. Si un fichier utilise HEVC (H.265) ou un profil audio non supporté (DTS-HD MA / TrueHD), recommander automatiquement le basculement vers MPV ou le transcodage léger.
