# KINO

[English](../README.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Español** · [Italiano](README.it.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [日本語](README.ja.md)

KINO es una aplicación de escritorio para macOS y Windows que reúne en un solo lugar un catálogo de películas, series y anime, el seguimiento de lo que ves y la reproducción, a través de tu propia cuenta en un servicio de debrid (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

La interfaz está disponible en inglés, francés, alemán, español, italiano, portugués, ruso, chino y japonés: KINO sigue el idioma de tu ordenador, y puedes cambiarlo en Ajustes → Aplicación → Idioma de la interfaz.

## Aviso

KINO no aloja, proporciona ni distribuye ningún contenido. Consulta las fuentes que elige el usuario y reproduce lo que devuelve su propio servicio de debrid. Cada usuario es el único responsable de lo que ve y del respeto de los derechos de autor en su país.

## Qué hace KINO

- **Catálogo**: películas, series y anime, con fichas, reparto, tráileres y cada título con su nombre original.
- **Sin servicio de debrid**: añade complementos (más fuentes y sus
  catálogos en Explorar) y reproduce torrents por P2P tras aceptar lo que implica
  (tu dirección IP es visible y compartes mientras ves).
- **Manga**: todo el catálogo y las series en publicación, cada capítulo
  reunido desde varios sitios (en tu idioma cuando existe, si no en inglés),
  tomos completos en HD por torrent y tus propios archivos CBZ y carpetas de
  imágenes; lectura página a página o en desplazamiento, en sentido japonés si
  quieres. KINO recuerda dónde lo dejaste.
- **Reproducción**: reproductor de vídeo integrado basado en mpv (HDR, subtítulos, pistas de audio, salto de la intro).
- **Seguimiento**: Mi lista, en curso, vistas, mis series, calendario de estrenos, valoraciones con un gesto.
- **Sugerencias**: «Para ti» y «Si te gustó», calculadas en tu ordenador a partir de lo que has visto y valorado.
- **Anime**: filas de AniList, programación semanal, recorrido de una saga en orden de estreno.
- **Otros servicios**: importación de Letterboxd, Trakt, presencia en Discord.
- **En compañía y en otro lugar**: Watch Party, reproducción en una TV de la red local, mando a distancia desde el teléfono.
- **Sin conexión**: descarga una película o una temporada para verla sin internet.
- **Tus datos**: guardados en tu ordenador, con una copia de seguridad diaria y sincronización opcional mediante Google Drive.

## Descargar KINO

| Sistema | Descargar |
|---|---|
| **macOS** — Mac con Apple Silicon (M1 o posterior), macOS 14 o posterior | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 u 11, 64 bits | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |
| **Linux** — x86_64: Ubuntu 22.04, Debian 12, Fedora 36 o posterior | [**KINO-Linux-x86_64.AppImage**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.AppImage) |

KINO contiene todo lo que necesita: reproductor de vídeo, FFmpeg, interfaz. No hay nada más que instalar. Después, pega la clave de tu servicio de debrid en Ajustes.

- **macOS**: abre el archivo `.dmg` y arrastra KINO a Aplicaciones.
- **Windows**: ejecuta `KINO-Windows.exe`. KINO se instala con un clic, sin permisos de administrador, y se abre.
- **Linux**: haz ejecutable `KINO-Linux-x86_64.AppImage` (Propiedades → Permisos, o `chmod +x`) y ábrelo. Los vídeos se reproducen en la ventana de KINO, o en mpv si está instalado. KINO avisa de las nuevas versiones: descarga el nuevo AppImage.

Las versiones siguientes se instalan desde KINO: **Ajustes → Actualizaciones**.

### Instalar con un solo comando

Este comando descarga la última versión, comprueba el archivo, instala KINO y lo abre. También sirve para actualizarlo.

macOS (app Terminal):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### Si el sistema bloquea el primer inicio

KINO todavía no está firmado por Apple ni por Microsoft. Tras una descarga desde el navegador:

- **macOS**: si KINO no se abre, ve a Ajustes del Sistema → Privacidad y seguridad y haz clic en **Abrir igualmente**.
- **Windows**: si aparece «Windows protegió su PC», haz clic en **Más información** y luego en **Ejecutar de todas formas**.

Los comandos de arriba evitan ambos mensajes.

## Verificar una descarga

Cada archivo de una versión lo construye GitHub Actions a partir del commit que
la versión indica. Su SHA-256 está en `SHA256SUMS.txt` y, desde la versión
posterior a la 1.9.0, una atestación de compilación indica qué workflow y qué
commit lo produjeron:

```bash
gh attestation verify KINO-macOS.dmg -R isleepverywellatnight/kino
```

## Privacidad

KINO no tiene servidor propio y no recopila nada. Habla directamente con los
servicios que conectas (tu servicio de debrid, Trakt, Discord, Google Drive),
con los catálogos públicos que lee (como Cinemeta, AniList, MangaDex y TMDB con
tu clave), con las fuentes que consulta (indexadores, tus complementos, sitios
de manga), con los pares BitTorrent cuando la reproducción P2P está activada, y
con GitHub para buscar una versión nueva al iniciar y cada seis horas.
Ajustes → Actualizaciones desactiva esa búsqueda.

## Dónde están tus datos

- `~/.kino_data.db`: biblioteca, historial, valoraciones y caché.
- `~/.rd_cinehub_config.json`: ajustes y claves de los servicios.
- `~/Library/Application Support/KINO` (macOS) o `%APPDATA%\KINO` (Windows): copias de seguridad, registro de errores. Las actualizaciones en curso están en `%LOCALAPPDATA%\KINO\Updates` en Windows.

Las claves se quedan en tu ordenador: no se copian ni se sincronizan. Desinstalar KINO no borra estos datos.

## Estructura del repositorio

| Carpeta | Contenido |
|---|---|
| `crates/` | Código de KINO en Rust: `kino` (el servidor) y `kino-desktop` (la aplicación) |
| `web/` | Interfaz (HTML, CSS, JavaScript) |
| `assets/` | Iconos y archivos incluidos con la aplicación |
| `packaging/` | Compilación de las aplicaciones, instaladores, scripts de instalación |
| `tools/` | Herramientas de compilación y publicación |
| `tests/` | Pruebas |
| `docs/` | Los README en otros idiomas |

Para compilar KINO o publicar una versión, consulta [packaging/README.md](../packaging/README.md).

## Licencia

KINO se publica bajo licencia MIT (ver [LICENSE](../LICENSE)). La carpeta `web/js/vendor` contiene PeerJS y QRCode.js, cada uno con su propia licencia MIT. mpv, libmpv y FFmpeg, incluidos en las aplicaciones, están bajo licencia GPL o LGPL.
