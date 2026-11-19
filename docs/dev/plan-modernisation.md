# Plan de modernisation de KINO (base : 1.9.2, `main` @ 949006d)

Ce plan répond à la mission « Modernisation, architecture & optimisation ».
Chaque chantier part de **ce que le code fait aujourd'hui**, mesuré sur `main`,
et non de la description de la mission. Plusieurs prémisses de la mission sont
fausses ou déjà traitées ; elles sont signalées pour ne pas refaire un travail
déjà fait ni ajouter de la complexité sans gain.

Règles qui valent pour tout le plan :

- **Aucune régression des données** : `~/.kino_data.db` et
  `~/.rd_cinehub_config.json` restent lisibles par la version précédente tant
  qu'une migration n'est pas explicitement déclarée « sans retour ».
- **Zéro télémétrie** : aucune dépendance qui émet sur le réseau hors des
  services que l'utilisateur a configurés.
- **Rust 2024, `clippy -D warnings`, pas de `unsafe` nouveau hors FFI**, chaque
  bloc `unsafe` avec son commentaire `SAFETY:` (convention déjà en place).
- **Une PR par étape**, CI verte (Rust, Windows UI, Build) avant fusion.

---

## 0. État mesuré et corrections de la mission

| Affirmation de la mission | Mesure sur `main` | Conséquence |
|---|---|---|
| ~36k lignes JS | **14,8k** lignes dans `web/js/*.js` (hors `i18n/`, `vendor/`) | Le frontend est moitié moins gros : une migration incrémentale est réaliste. |
| ~39k lignes Rust | 40,9k | Conforme. |
| Templates `innerHTML` bruts | 204 `innerHTML` + 4 `insertAdjacentHTML/outerHTML` ; un `escapeHtml` existe (`app-social.js:5`) | Le risque est réel mais l'échappement existe déjà, à la main. |
| Handlers inline | **Supprimés** (PR #18–#20) : actions déclarées `act()/actOn()` et politique de scripts (CSP) sur `/` et `/remote` | Chantier 1 hérite d'une base saine ; ne pas casser la CSP. |
| `window.pywebview.api` | Nom historique conservé : le pont est fourni par `crates/kino-desktop/src/bridge.rs` (Wry), appelé depuis 6 fichiers JS | Le typer est simple : la surface est petite et unique. |
| Rusqlite bloque Tokio | `Db` a déjà **un écrivain + un pool de lecteurs** (`db.rs`, PR #14) ; 57 `spawn_blocking` dans le code, 38 dans `routes/` | Il reste un **audit**, pas une refonte. |
| Pas de logs structurés | `errorlog.rs` : journal local, rotation (512 Ko × 2), masquage des URL/magnets/clés/e-mails, dédoublonnage ; **3** `eprintln!` seulement | `tracing` n'apporte presque rien ici ; voir chantier 5. |
| 155 `let _ = …` silencieux | 155 occurrences | À trier : beaucoup sont légitimes (envoi sur un canal fermé, `set_permissions`). |
| Clés en clair | Confirmé : `provider_tokens` dans `~/.rd_cinehub_config.json` (chmod 600 sous Unix), jetons Trakt dans `app_config` de la base | Le chantier 4 doit couvrir **les deux** emplacements. |
| i18n par `MutationObserver` | Confirmé : `web/js/i18n.js:142`, catalogues indexés par **la phrase française** (`web/js/i18n/*.js`, `strings.json`) | Chantier 2 justifié. |
| mpv Windows docké par `SetParent` | Confirmé : `win_dock.rs` (112 lignes) reparent la fenêtre de `mpv.exe`, pilotage par tube nommé `\\.\pipe\kino_mpv` | Chantier 3 justifié. |
| Signature | macOS : `codesign --verify` seulement, pas de notarisation ; Windows : pas d'Authenticode ; mises à jour signées Ed25519 par KINO | Chantier 6.3 justifié. |
| Linux | Tout le workspace (desktop compris, avec WebKitGTK) est compilé et testé sur `ubuntu-latest` par `rust.yml` ; rien n'est empaqueté ni publié | Chantier 6.1 = empaquetage, pas portage. |

---

## 1. Architecture cible

```
┌────────────────────────── kino-desktop (Tao + Wry) ──────────────────────────┐
│  Fenêtre native            Pont IPC typé (bridge.rs)        Lecteur natif     │
│  win_frame / mac_window ── window.pywebview.api.* ───────── libmpv (dlopen)   │
│                            schéma unique : bridge.d.ts      mac : render API  │
│                                                              win : wid=HWND   │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │ HTTP local (CSP, anti-rebinding, Origin/Sec-Fetch-Site)
┌──────────────▼──────────────── kino (Axum, Tokio) ───────────────────────────┐
│ routes/*  ─ services (catalog, play, p2p, cloud, sync…) ─ Db (1 écrivain,     │
│                                                          N lecteurs, WAL)    │
│ SecretStore (Keychain / Credential Manager / Secret Service, repli fichier)  │
│ errorlog (journal local masqué)        sync : Drive | WebDAV | dossier       │
└──────────────────────────────────────────────────────────────────────────────┘
               ▲
┌──────────────┴──────────────── web/ (servi tel quel, sans build) ────────────┐
│ <script type="module"> ; modules ES natifs ; JSDoc + `tsc --noEmit` en CI ;  │
│ html`` auto-échappé ; t('clé.sémantique') + data-i18n ; remote.html manifeste │
└──────────────────────────────────────────────────────────────────────────────┘
```

Choix structurant : **pas de bundler**. La WebView (WebView2, WKWebView,
WebKitGTK) charge les modules ES nativement ; le serveur sert déjà `web/` avec
une CSP stricte. Vite/esbuild ajouterait une étape de build, une
chaîne Node dans la release et un écart entre le code servi et le code testé,
pour un gain nul en runtime (fichiers locaux, pas de latence réseau). Le
typage passe par **JSDoc + `// @ts-check`**, vérifié par `tsc --noEmit` en CI
(dépendance de développement seulement, rien dans le produit).

---

## 2. Spécifications par chantier

### Chantier 1 — Frontend ESM, typage, rendu sûr (priorité haute)

**1.1 Modules ES sans bundler.** Les 25 `<script>` de `web/index.html`
deviennent un seul `<script type="module" src="js/main.js">`. Chaque fichier
exporte ce qu'il fournit et importe ce qu'il utilise. Les 938 déclarations de
haut niveau ne sont pas toutes partagées : on n'exporte que celles qu'un autre
fichier appelle (le graphe est déjà calculé par `tools/frontend_check.js`).

Point dur : les actions déclarées (`act('play', …)`) résolvent un **nom
global**. On les remplace par un registre explicite, ce qui supprime la
dernière dépendance à `window` :

```js
// web/js/actions.js
// @ts-check
/** @type {Map<string, (...args: unknown[]) => unknown>} */
const registry = new Map();

/** @param {Record<string, (...args: any[]) => unknown>} handlers */
export function registerActions(handlers) {
  for (const [name, fn] of Object.entries(handlers)) {
    if (registry.has(name)) throw new Error(`action en double : ${name}`);
    registry.set(name, fn);
  }
}

export function runAction(name, args, event) {
  const fn = registry.get(name);
  if (!fn) throw new Error(`action inconnue : ${name}`);
  return fn(...args, event);
}
```

Chaque module appelle `registerActions({ playReadyInstead, selectGenre, … })`.
Le test d'intégrité (`test_page_actions`) vérifie déjà que chaque nom déclaré
existe ; il lira le registre au lieu de `window`.

**1.2 Typage du pont IPC.** Un seul fichier de types, à côté du pont :

```ts
// web/js/types/bridge.d.ts
export interface WindowState { fullscreen: boolean; maximized: boolean }
export interface KinoBridge {
  minimize(): Promise<void>;
  toggle_maximize(): Promise<void>;
  toggle_fullscreen(): Promise<boolean>;
  get_state(): Promise<WindowState>;
  platform(): Promise<'macos' | 'windows' | 'linux'>;
  choose_manga_folder(): Promise<string | null>;
  check_for_updates(): Promise<unknown>;
  install_update(): Promise<unknown>;
  // … une ligne par variante de l'enum d'appels de crates/kino-desktop/src/bridge.rs
}
declare global { interface Window { pywebview?: { api: KinoBridge } } }
```

`bridge.rs` décode déjà chaque appel en une variante d'enum par nom
(`"toggle_maximize" => Self::ToggleMaximize`, ~30 noms). Un test lit ces noms
et échoue si `bridge.d.ts` en oublie un ou en invente un (même principe que
les tests d'intégrité déjà en place). Les types de retour exacts sont relevés
dans le code au moment de l'écrire ; ceux ci-dessus sont indicatifs. On ne
génère pas les types depuis Rust (`ts-rs`/`specta`) : pour ~30 appels, un test
de cohérence coûte moins qu'un générateur.

**1.3 Rendu auto-échappé.** Un tag `html` remplace la concaténation :

```js
// web/js/html.js
// @ts-check
const RAW = Symbol('raw');
/** Fragment déjà sûr (sortie d'un autre html``). */
export const raw = (s) => ({ [RAW]: String(s) });

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escape = (v) => String(v).replace(/[&<>"']/g, (c) => ESC[c]);

function part(v) {
  if (v == null || v === false) return '';
  if (Array.isArray(v)) return v.map(part).join('');
  if (typeof v === 'object' && RAW in v) return v[RAW];
  return escape(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => { out += part(v) + strings[i + 1]; });
  return raw(out);
}

/** Seule porte d'entrée vers le DOM. */
export function render(el, fragment) {
  el.innerHTML = fragment[RAW];
}
```

Les valeurs dans un attribut `href`/`src` passent par `safeUrl()` (schémas
`http`, `https`, `data:image/` seulement). Une règle de `frontend_check.js`
interdit ensuite toute affectation `innerHTML =` hors de `html.js`. La CSP
déjà en place reste la deuxième barrière.

**Fichiers** : `web/index.html`, `web/remote.html`, `web/js/*.js`,
nouveaux `web/js/main.js`, `web/js/html.js`, `web/js/types/*.d.ts`,
`jsconfig.json` (`checkJs`, `strict`), `tools/frontend_check.js`,
`tools/page_harness.js` (charger des modules), workflow `rust.yml` (étape
`npx -y typescript@5 tsc -p jsconfig.json`).

### Chantier 2 — i18n déterministe (priorité haute)

État : l'interface est écrite en français ; `i18n.js` observe le DOM et
remplace chaque texte par sa traduction trouvée **par la phrase française**
(et par regex pour les phrases à variables). Un changement de ponctuation en
français casse silencieusement les 8 autres langues, et le texte français
s'affiche un instant avant d'être remplacé.

Cible : des clés sémantiques, résolues **au rendu**.

```js
// web/js/i18n.js
// @ts-check
import fr from './i18n/fr.js';
/** @type {Record<string, string>} */
let table = fr;

export async function setLocale(code) {
  table = code === 'fr' ? fr : { ...fr, ...(await import(`./i18n/${code}.js`)).default };
  document.documentElement.lang = code;
  applyStatic(document);
}

/** t('library.count', { n: 3 }) — repli sur le français, jamais sur la clé seule. */
export function t(key, vars = {}) {
  const text = table[key] ?? fr[key];
  if (text === undefined) throw new Error(`clé i18n inconnue : ${key}`);
  return text.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''));
}

/** Textes fixes de index.html : <button data-i18n="player.controls.play">. */
export function applyStatic(root) {
  for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll('[data-i18n-title]')) el.title = t(el.dataset.i18nTitle);
}
```

Les pluriels passent par `Intl.PluralRules` (une clé `.one`/`.other`), les
dates par `Intl.DateTimeFormat` au lieu de chaînes comme
`"%d/%m/%Y à %H:%M"`.

**Migration incrémentale sans perdre les 9 langues** :

1. Un script `tools/i18n_keys.js` génère `i18n/fr.js` en attribuant une clé à
   chaque entrée de `strings.json` (clé proposée depuis le fichier et
   l'élément où la phrase apparaît, relue à la main), puis réécrit chaque
   catalogue `en.js … zh.js` de `"phrase française": "traduction"` vers
   `"clé": "traduction"`. Aucune traduction n'est retapée.
2. Pendant la transition, l'observateur reste actif **uniquement** pour les
   textes sans `data-i18n` ni passage par `t()` ; un compteur de diagnostic
   liste ce qu'il traduit encore.
3. Écran par écran (même découpage que la refonte : Accueil, Fiche,
   Bibliothèque, Paramètres, lecteurs), les textes passent à `t()` /
   `data-i18n`.
4. Quand le compteur est à zéro sur tous les écrans du harnais, on retire
   l'observateur et les regex.

Test bloquant en CI : chaque catalogue a exactement les clés de `fr.js`
(une clé manquante est signalée, pas silencieusement remplacée), et chaque
`t('…')`/`data-i18n` du code existe dans `fr.js`.

### Chantier 3 — libmpv sous Windows

État : `player.rs` lance `mpv.exe` et le pilote par tube nommé ;
`win_dock.rs` cherche sa fenêtre par PID (attente jusqu'à ~2 s), la passe en
`WS_CHILD` de la fenêtre KINO et suit les redimensionnements. Les défauts
(focus clavier volé, course au démarrage, fenêtre d'un autre processus comme
enfant) viennent de ce reparentage inter-processus.

Cible : **libmpv dans le processus, dessinant dans la fenêtre de KINO**
(`wid`). Un essai (18/11/2026) a montré que la page WebView2, au fond
transparent, s'affiche bien par-dessus la vidéo que libmpv dessine dans la
fenêtre principale : les contrôles de la page restent ceux du Mac. C'est le mode d'intégration documenté de mpv sous
Windows ; il évite l'API de rendu.

Pourquoi pas une swapchain DirectX ou WGL avec l'API de rendu de mpv, comme
le propose la mission : sous macOS, KINO utilise l'API de rendu parce qu'une
`NSView` OpenGL doit cohabiter avec la `WKWebView`. Sous Windows, WebView2 et
une fenêtre enfant Win32 cohabitent déjà par l'ordre Z ; l'API de rendu
imposerait de gérer contexte GL/D3D, vsync et redimensionnement, soit des
centaines de lignes `unsafe` en plus pour le même résultat à l'écran, et
perdrait le `gpu-next`/`d3d11` natif de mpv.

```rust
// crates/kino-desktop/src/win_mpv.rs (esquisse)
pub struct Mpv {
    api: Api,                // symboles chargés par libloading, comme mac_mpv.rs
    handle: *mut MpvHandle,
    child: HWND,             // fenêtre créée par KINO, enfant de la fenêtre principale
}

impl Mpv {
    pub fn new(parent: HWND, dll: &Path) -> anyhow::Result<Self> {
        let api = Api::load(dll)?;                         // libmpv-2.dll
        let child = create_child_window(parent)?;          // CreateWindowExW(WS_CHILD|WS_CLIPSIBLINGS)
        // SAFETY: mpv_create n'a pas de précondition ; null est traité.
        let handle = unsafe { (api.create)() };
        anyhow::ensure!(!handle.is_null(), "mpv_create a échoué");
        let mpv = Self { api, handle, child };
        mpv.set_option("wid", &(child as isize).to_string())?;
        mpv.set_option("vo", "gpu-next")?;
        mpv.set_option("input-default-bindings", "yes")?;
        mpv.initialize()?;
        Ok(mpv)
    }
}

impl Drop for Mpv {
    fn drop(&mut self) {
        // SAFETY: handle vient de mpv_create et n'est détruit qu'ici.
        unsafe { (self.api.terminate_destroy)(self.handle) };
        destroy_child_window(self.child);
    }
}
```

Le code commun avec `mac_mpv.rs` (chargement des symboles, commandes,
propriétés observées, boucle d'événements) sort dans un module `mpv_api.rs`
partagé ; seul l'affichage diffère (render API sur macOS, `wid` sous Windows).
Les commandes passent par `mpv_command` au lieu du tube nommé, ce qui retire
`connect()`/le tube de `player.rs` pour Windows.

**Synchronisation avec WebView2** : la fenêtre enfant mpv est placée sous les
contrôles HTML ou au-dessus selon le mode actuel ; ses dimensions suivent
`WindowEvent::Resized` de Tao (déjà écouté pour le dock). Le clavier reste à
la WebView, qui transmet les raccourcis par le pont (`keypress` mpv), au lieu
de donner le focus à mpv.

**Distribution** : `tools/fetch_mpv_windows.ps1` récupère `libmpv-2.dll`
(build « dev » des mêmes sources) au lieu de `mpv.exe`. Le lecteur externe
`mpv.exe` reste disponible en option « Lecteur externe » le temps d'une
version, puis `win_dock.rs` est supprimé.

**Recette** : test `windows-ui.yml` en vraie fenêtre (lecture d'un clip de
test, redimensionnement, plein écran, Échap, fermeture), plus un essai manuel
sur la machine de l'utilisateur.

### Chantier 4 — `SecretStore` (trousseau du système)

Secrets concernés : `provider_tokens` (Real-Debrid, AllDebrid, TorBox… dans
le fichier de config) et `trakt_tokens` (dans `app_config` de la base). Le
dossier Google Drive est un chemin local, pas un jeton.

Dépendance : `keyring = { version = "3", features = ["apple-native",
"windows-native", "sync-secret-service"] }`. C'est la crate standard, sans
réseau. `thiserror` devient une dépendance directe (il est déjà dans l'arbre
par d'autres crates). Sous Linux, Secret Service exige un démon (GNOME Keyring, KWallet) :
s'il est absent, on reste sur le fichier (chmod 600), avec un avertissement
dans le Diagnostic.

```rust
// crates/kino/src/secrets.rs
use keyring::Entry;

const SERVICE: &str = "app.kino";

#[derive(Debug, thiserror::Error)]
pub enum SecretError {
    #[error("trousseau indisponible : {0}")]
    Unavailable(#[source] keyring::Error),
}

pub trait SecretStore: Send + Sync {
    fn get(&self, name: &str) -> Result<Option<String>, SecretError>;
    fn set(&self, name: &str, value: &str) -> Result<(), SecretError>;
    fn delete(&self, name: &str) -> Result<(), SecretError>;
}

pub struct OsKeyring;

impl SecretStore for OsKeyring {
    fn get(&self, name: &str) -> Result<Option<String>, SecretError> {
        let entry = Entry::new(SERVICE, name).map_err(SecretError::Unavailable)?;
        match entry.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(SecretError::Unavailable(e)),
        }
    }
    fn set(&self, name: &str, value: &str) -> Result<(), SecretError> {
        Entry::new(SERVICE, name)
            .and_then(|e| e.set_password(value))
            .map_err(SecretError::Unavailable)
    }
    fn delete(&self, name: &str) -> Result<(), SecretError> {
        match Entry::new(SERVICE, name).and_then(|e| e.delete_credential()) {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(SecretError::Unavailable(e)),
        }
    }
}
```

Le trait n'existe que parce qu'il y a deux implémentations réelles : le
trousseau et un magasin en mémoire pour les tests (les tests ne doivent jamais
toucher le trousseau de la machine).

`AppConfig` garde ses champs ; seule la persistance change : `config::save`
écrit les jetons dans le `SecretStore` et `from_files` les relit, avec repli
sur le fichier.

**Migration (expand → migrate → verify → contract)** :

1. *Expand* (version N) : lecture trousseau puis fichier ; écriture **dans
   les deux**. Une version N-1 (retour en arrière par mise à jour) continue
   de fonctionner.
2. *Migrate* (N, au démarrage) : chaque jeton du fichier absent du trousseau
   y est copié ; idempotent, rejoué à chaque lancement tant qu'il manque
   quelque chose ; un échec est écrit dans `errorlog` et la clé reste lisible
   dans le fichier.
3. *Verify* : le Diagnostic affiche « clés : trousseau / fichier » par
   fournisseur.
4. *Contract* (version N+1, **décision séparée de l'utilisateur**) : le
   fichier ne garde plus que `"provider_tokens": {"real_debrid": "@keyring"}`.
   À partir de là, revenir à une version antérieure à N fait perdre l'accès
   aux clés (il faudra les ressaisir). C'est la seule étape sans retour
   automatique du plan.

Fait en P7 (18/11/2026) : dépendance `keyring` 3.6.3, seulement pour Windows
et Linux (sans fonctionnalité de plateforme, la crate retomberait sur un faux
magasin en mémoire). Les entrées s'appellent `debrid:<service>@<empreinte>`,
`tmdb_key@…`, `trakt_tokens@…`, où l'empreinte vient du chemin du fichier de
réglages : un KINO lancé avec un autre dossier personnel (tests, autre compte)
ne lit ni n'efface les clés du vrai. Seuls les exécutables de KINO activent le
trousseau ; les tests restent sur le fichier.

Corrigé le 18/11/2026 (accord de l'utilisateur) : la synchro Drive
(`gdrive.rs`) exportait toutes les lignes `app_config`, donc `trakt_tokens` en
clair dans `kino_sync.json`. Les jetons Trakt ne voyagent plus, ni à l'export
ni à l'import d'un fichier écrit par une version plus ancienne ; chaque
ordinateur se connecte à Trakt de son côté.

Sous macOS, les mises à jour remplacent le bundle : l'accès au trousseau est
lié à la signature. Une signature ad hoc qui change à chaque build ferait
redemander l'accès à chaque mise à jour. **La phase 4 dépend donc de la
signature Developer ID (chantier 6.3) sur macOS.**

### Chantier 5 — Concurrence SQLite et observabilité

**5.1 Mesurer, puis corriger les appels lents.** `Db` sépare déjà l'écrivain
et les lecteurs. Mais une cinquantaine de handlers async appellent la base
directement (`routes/library.rs`, etc.) : ce sont surtout des lectures de
quelques microsecondes, que les envelopper toutes dans `spawn_blocking` ne
rendrait pas plus rapides. Ce qui compte, ce sont les appels **lents** sur un
thread async (FTS5, agrégations d'historique, attente du verrou d'écriture).

Fait en P0 (`crates/kino/src/db.rs`) :

- `db::spawn_blocking` remplace `tokio::task::spawn_blocking` dans tout le
  serveur ; il marque le thread comme « bloquant autorisé ».
  `db::blocking(…)` fait la même chose pour les threads lancés autrement
  (les appels du pont de l'app desktop).
- `Db::with` et `Db::read` mesurent chaque accès. Un accès de **25 ms ou
  plus**, sur un thread qui exécute des tâches async et n'est pas marqué, est
  écrit dans le journal d'erreurs : « Base lente sur un thread async »,
  avec le fichier et la ligne de KINO qui l'a demandé (`#[track_caller]`) et
  la durée. Rien n'est écrit pour les accès rapides.

La P4 part de ces entrées, relevées sur un vrai usage : chaque endroit signalé
passe par `db::spawn_blocking`, ou son calcul part dans un service déjà appelé
hors du runtime. Un garde qui ferait échouer tout accès depuis un thread async
(idée initiale) aurait imposé de réécrire 50 appels rapides sans gain.

**5.2 Journalisation.** Je recommande de **ne pas** introduire `tracing` :
`errorlog.rs` fait déjà ce dont KINO a besoin (local, rotation, masquage des
secrets, dédoublonnage, lu par le Diagnostic). `tracing-subscriber` +
`tracing-appender` ajouteraient une seconde voie de journalisation qui ne
masque rien par défaut. À faire à la place :

- trier les 155 `let _ = …` : ceux qui ignorent une erreur d'E/S ou de base
  qui compte (ex. `trakt.rs:137`, jetons Trakt non sauvés) passent par
  `errorlog::record` ; les autres reçoivent un commentaire d'une ligne ;
- les 3 `eprintln!` passent par `errorlog` ;
- ajouter un niveau `debug` à `errorlog`, actif seulement avec
  `KINO_DEBUG=1`, pour le diagnostic à la demande.

Si un jour un traçage par requête devient nécessaire (temps par route),
`tracing` sera réévalué avec une couche qui écrit **dans** `errorlog`.

### Chantier 6 — Distribution

**6.1 Linux.** `kino-desktop` compile déjà sous Linux (WebKitGTK 4.1, CI
`rust.yml`). Reste à vérifier l'app réelle (le lecteur passe par `mpv` externe
en fenêtre séparée, le temps d'avoir `wid` sur X11), puis **AppImage** (un fichier, fonctionne sur
Steam Deck en mode bureau), puis **Flatpak** (`org.freedesktop.Platform`,
extension `io.mpv` ou mpv construit dans le manifeste, permissions réseau,
`--talk-name=org.freedesktop.secrets` pour le trousseau). Le serveur local
écoute sur le LAN pour la télécommande et la TV : à déclarer dans le
manifeste. Pas de Snap (deux formats suffisent).

**6.2 Windows ARM64.** Ajouter `aarch64-pc-windows-msvc` à la matrice de
`build.yml` sur le runner `windows-11-arm` (disponible pour les dépôts
publics). Points à vérifier : `ring`/`rustls` (OK en ARM64), `rusqlite`
`bundled` (compile avec MSVC ARM64), libmpv ARM64 (builds disponibles chez
les mêmes fournisseurs ; sinon le décodage passe par l'émulation, ce qui
annule le gain). L'installeur Inno Setup (`kino.iss`) reçoit
`ArchitecturesAllowed=arm64`. Prérequis : chantier 3, pour n'avoir qu'une
DLL à fournir.

**6.3 Signature et notarisation.**

- macOS : certificat Developer ID Application, secrets CI
  `APPLE_CERT_P12`, `APPLE_CERT_PASSWORD`, `APPLE_TEAM_ID`,
  `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`, `APPLE_API_KEY_P8` ; étapes
  `codesign --options runtime --entitlements … --timestamp`, puis
  `xcrun notarytool submit --wait`, puis `xcrun stapler staple` sur l'app et
  le DMG. Entitlements : `com.apple.security.cs.disable-library-validation`
  (libmpv chargé depuis le bundle) si la signature de libmpv diffère.
  Coût : 99 $/an.
- Windows : SmartScreen se base sur la réputation du certificat. Option
  recommandée : **Azure Trusted Signing** (~10 $/mois, réputation acquise
  vite, intégrable en CI via `azure/trusted-signing-action`) ; alternative
  certificat OV classique sur jeton matériel. Signer `KINO.exe`,
  `libmpv-2.dll`, `ffmpeg.exe` et l'installeur.
- La signature Ed25519 des mises à jour KINO reste en place ; elle est
  indépendante des signatures de plateforme.

Ces étapes nécessitent des comptes payants et des secrets que seul le
propriétaire du dépôt peut créer : **décision et action de l'utilisateur**.

### Chantier 7 — Fonctionnalités

**7.1 Télécommande « installable ».** Contrainte décisive : un Service
Worker et l'installation PWA exigent un **contexte sécurisé** (HTTPS ou
`localhost`). La télécommande est servie en `http://192.168.x.y:port` : le
navigateur du téléphone **refusera le Service Worker**. Un certificat
auto-signé déclencherait un avertissement bloquant ; un certificat public
pour une IP locale est impossible. Et un cache hors ligne n'aurait pas
d'usage : sans le Mac/PC, la télécommande ne pilote rien.

Ce qui est faisable et utile :

- `/remote/manifest.webmanifest` (`display: standalone`, icônes, couleur de
  thème) et `apple-mobile-web-app-capable` : « Ajouter à l'écran d'accueil »
  ouvre la télécommande en plein écran, sans barre de navigateur (iOS
  l'accepte en HTTP ; Android crée un raccourci) ;
- retour haptique : `navigator.vibrate` est déjà utilisé
  (`web/js/remote.js:7`) ; iOS ne l'expose pas, rien à faire de plus ;
- mise en page tactile : zones de 48 px minimum, gestes de balayage pour
  avancer/reculer, `viewport-fit=cover` ;
- reconnexion automatique quand le serveur revient.

La route du manifeste s'ajoute à la liste des fichiers que les téléphones ont
le droit de charger (`routes/remote.rs` + contrôle d'accès existant).

**7.2 Synchronisation WebDAV / dossier local.** `gdrive.rs` synchronise déjà
**par un fichier** (`kino_sync.json`) placé dans le dossier Drive local, avec
fusion et marqueurs de suppression datés. Le transport est donc déjà « un
dossier ». Cible :

- extraire la fusion (indépendante du transport) dans `sync.rs` ;
- `trait SyncTarget { fn read(&self) -> Result<Option<Vec<u8>>>; fn
  write(&self, bytes: &[u8], if_match: Option<&str>) -> Result<Etag>; }` avec
  trois implémentations réelles : **dossier** (Drive, Nextcloud Desktop,
  Syncthing, iCloud Drive : ce que fait déjà Drive), **WebDAV** (reqwest :
  `GET`, `PUT` avec `If-Match` pour éviter d'écraser une écriture
  concurrente) ;
- identifiants WebDAV dans le `SecretStore` (chantier 4).

Le mode « dossier » couvre Nextcloud sans WebDAV ; WebDAV n'est utile que
sans client de synchronisation installé.

**7.3 Profils locaux.** Changement de schéma le plus risqué du plan.

- Nouvelle table `profiles(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  created_at REAL)` ; profil `1` créé avec le nom de l'utilisateur.
- Tables par profil : `watchlist`, `watch_history`, `tonight_feedback`, les
  notes, la progression de lecture. Leur clé primaire `id` devient
  `(profile_id, id)` : SQLite ne sait pas modifier une clé primaire, il faut
  **reconstruire** chaque table (créer `watchlist_new`, copier avec
  `profile_id = 1`, renommer), dans une transaction.
- Tables partagées : `media_catalog`, `media_aliases`, `persistent_cache`,
  `app_config` (réglages machine), téléchargements hors ligne.
- Le code n'a pas aujourd'hui de numéro de version de schéma
  (`CREATE TABLE IF NOT EXISTS` uniquement) : introduire
  `PRAGMA user_version` et une liste ordonnée de migrations **avant** les
  profils.
- **Sauvegarde obligatoire** avant la migration : `backup.rs` existe déjà
  (copie quotidienne, 7 conservées) ; la migration en déclenche une et refuse
  de continuer si elle échoue.
- **Retour arrière** : une version antérieure lirait une table `watchlist`
  avec une colonne en plus et une clé composite ; ses `INSERT … ON CONFLICT
  (id)` échoueraient. La migration est donc **sans retour** sauf
  restauration de la sauvegarde. À faire seulement sur décision explicite.
- La synchronisation (7.2) devient par profil.

---

## 3. Plan de migration par phases

Ordre choisi pour que chaque phase rende la suivante plus sûre, et que les
étapes sans retour arrivent en dernier.

| Phase | Contenu | Dépend de | Définition de « fini » |
|---|---|---|---|
| **R** Fin de la refonte ✅ | Bibliothèque (ancienne session), fenêtres/messages/états vides (étape 7), puis une seule feuille : `style.css` et `redesign.css` fondues dans `design.css` dans leur ordre d'origine, 57 règles mortes retirées, 34 tailles sous 12 px remontées (étape 8) | — | page chargée avec `design.css` seule ; styles calculés identiques sur ~8 000 éléments (accueil, Bibliothèque, réglages, fenêtre, lecteur) hormis les tailles remontées |
| **P0** Socle ✅ | `jsconfig.json` + `tsc` **bloquant** en CI sur les fichiers marqués `// @ts-check` (9 fichiers au départ) ; types partagés dans `web/types/globals.d.ts` ; mesure des accès SQLite lents (5.1) ; `PRAGMA user_version` à 1 sans changement de tables | — | CI verte ; base existante passée à `user_version = 1` sans ligne modifiée (test) ; accès lents signalés dans le journal |
| **P1** Rendu sûr ✅ | `web/js/html.js` : `html```` échappe toute valeur qui n'est pas un fragment, `escapeHtml`/`act`/`actOn`/`attrJson` en renvoient, `render`/`replaceWith`/`renderAdjacent` sont les seules écritures (une chaîne y entre comme texte) ; 360 templates étiquetés, 200 écritures converties, liens et cadres par `safeUrl` ; règle `htmlWrites` du contrôle d'intégrité | P0, R | 0 écriture de HTML hors `html.js` (hors `document.write` des dictionnaires d'`i18n.js`, jusqu'à P3) ; titre piégé affiché en texte (tests) ; parcours de 15 onglets, 5 fenêtres, recherche, fiche et lecteur sans balisage visible ni erreur |
| **P2** Modules ES ✅ | Fait : état partagé par setters (`setCurrentMedia`…) et objets `playerPrefs`/`linkedAccounts` au lieu de `window.*` ; registre d'actions (`registerActions`, vérifié par `frontend_check`) ; démarrage dans `startKino()` ; `main.js` seul module de `index.html`, `remote.js` de `remote.html`, chaque fichier importe ce qu'il utilise ; `web/types/bridge.d.ts` + `test_bridge_types.js` ; tous les fichiers de `web/js` vérifiés par TypeScript (`checkJs`), ce qui remplace `names_check` | P1 | `index.html` et `remote.html` : un seul `type="module"` ✅ ; 0 accès `window.*` hors pont ✅ (hors `KINO_LANG`/`KINO_LOCALE`/`KINO_LANGUAGES` d'`i18n.js`, script classique jusqu'à P3) ; `tsc` bloquant sur tous les fichiers ✅ ; CSP inchangée ✅ ; vraie fenêtre Windows UI verte ✅ |
| **P3** i18n (en cours) | Fait : une clé par texte (`web/js/i18n/fr.js`, 2 140 clés ; les 8 catalogues indexés par clé, sans traduction retapée, page identique dans Chromium en EN/JA/FR) ; `KINO_I18N.t(clé, …valeurs)`, `data-i18n`/`-title`/`-placeholder`/`-aria-label`, `KINO_I18N.remaining()` (textes que l'observateur traduit encore au démarrage, en anglais : 458, puis 32) ; `test_i18n_catalogs.js` bloquant ; texte fixe d'`index.html` par clé (438 textes, 129 attributs ; une clé ne s'applique que tant que l'élément porte son français, ce que le code y écrit reste à l'observateur). 424 textes fixes des templates JS par `KINO_I18N.t()` et 306 textes à partie variable par `KINO_I18N.t(clé, …valeurs)` / `tHtml()` (14 écrans, avec données, identiques à avant dans les 9 langues hormis les traductions que l'observateur manquait). Reste : chaînes de libellés hors templates (branches de ternaire, étiquettes d'objets ; à trier une à une, car les mêmes littéraux servent aussi de valeurs envoyées au serveur), 63 textes encore traduits par l'observateur sur 14 écrans en anglais, textes mêlés à une icône dans `index.html`, `anime-journey.js`, messages du serveur, retrait de l'observateur | P2 | 0 texte traduit par l'observateur ; catalogues complets (test) ; captures FR/EN/JA identiques à avant, sans clignotement |
| **P4** Journal & SQLite | tri des `let _`, `eprintln!` → `errorlog`, correction des accès lents signalés par P0 | P0 + quelques jours d'usage réel | plus d'entrée « Base lente » en usage normal ; aucune erreur ignorée sans commentaire |
| **P5** Lecteur Windows (en cours) | `mpv.rs` commun (client libmpv + contrôleur), `mac_mpv.rs` = rendu OpenGL seul, `win_mpv.rs` (`wid` dans la fenêtre KINO, WebView2 transparente) ; `libmpv-2.dll` livrée ; `mpv.exe` reste en lecteur externe | — | Windows UI : lecture native, vidéo visible sous la page, vrai clic sur Pause, fermeture ; essai par l'utilisateur ; `win_dock.rs` et `mpv.exe` retirés à la version suivante |
| ~~P6~~ Signature | **Reportée** (pas de comptes payants pour l'instant). Developer ID + notarisation, Trusted Signing | comptes de l'utilisateur | `spctl --assess` accepte l'app ; installeur Windows sans avertissement SmartScreen « éditeur inconnu » |
| **P7** Secrets, expand ✅ | `secrets.rs` : clés des débrideurs, clé TMDB et jetons Trakt copiés dans le trousseau (Windows : Gestionnaire d'identification ; Linux : Secret Service), écrits aux deux endroits, lus depuis le fichier ; le format `@keychain` (P10) est déjà compris à la lecture ; copie au démarrage, idempotente ; Diagnostic « keys » ; macOS inchangé | — | tests sur magasin en mémoire ; aller-retour réel sur le Gestionnaire d'identification ; entrées rattachées au fichier de réglages (une autre instance de KINO ne voit ni n'efface les clés) |
| **P8** Distribution (en cours) | Linux : fenêtre (`build_gtk`) et barre de KINO ✅, AppImage x86_64 ✅ (`packaging/build_linux_appimage.sh`, construit sur ubuntu-22.04, WebKitGTK et plugins GStreamer embarqués, testé en CI sur écran virtuel : self-check, démarrage, vidéo H.264/HEVC dans la page ; publié depuis 2.1.0). Reste : mise à jour de l'AppImage en place, Flatpak, Windows ARM64 | P5 | artefacts publiés par la CI, `--self-check` OK sur chaque cible |
| **P9** Télécommande & sync | manifeste + tactile ; `sync.rs`, dossier, WebDAV | P7 (identifiants WebDAV) | ajout à l'écran d'accueil iOS/Android ; sync aller-retour entre deux machines via WebDAV et via dossier |
| **P10** Secrets, contract | fichier sans clés (Windows, Linux ; macOS après P6) | P7 publié depuis une version ; **gardée par l'utilisateur, confirmation avant publication** | plus aucune clé en clair sur ces OS ; procédure de ressaisie documentée |
| **P11** Profils | migrations `user_version` 2, tables reconstruites, UI de choix de profil | P0, P9 ; **gardée par l'utilisateur, confirmation avant publication** | migration testée sur une copie de la vraie base ; sauvegarde automatique vérifiée ; restauration testée |

## 4. Décisions (18/11/2026)

| # | Sujet | Décision |
|---|---|---|
| 1 | Outillage frontend | **Modules ES natifs + `tsc --noEmit` en CI**, pas de bundler. |
| 2 | Signature payante | **Aucune pour l'instant.** P6 reportée ; le trousseau ne couvre que Windows et Linux, macOS garde le fichier chmod 600. |
| 3 | Refonte visuelle | **Refonte d'abord** (phase R), modernisation ensuite sur des templates stables. |
| 4 | Étapes sans retour | **P10 et P11 gardées**, chacune confirmée avant publication. |
| 5 | Journalisation | Proposé : pas de `tracing`, `errorlog` étendu. Retenu sauf objection. |
| 6 | Lecteur Windows | Proposé : libmpv par `wid`. Retenu sauf objection. |
| 7 | Télécommande | Manifeste + écran d'accueil, sans Service Worker (contrainte technique, pas un choix). |
