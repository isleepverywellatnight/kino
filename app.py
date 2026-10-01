#!/usr/bin/env python3
"""
RD CineHub — Application Web & Serveur local pour chercher des Films/Séries
et les télécharger / streamer via Real-Debrid Premium.
Aucune dépendance externe requise (Python 3 standard).
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import kino_db
import torrent_engine
import remote_controller
import trakt_engine
import addon_manager
import anime_engine
import community_lists

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PORT = 8080
CONFIG_FILE = Path.home() / ".rd_cinehub_config.json"
PLAYLIST_FILE = Path.home() / ".kino_playlist.m3u"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

DOWNLOADS = {}
DOWNLOADS_LOCK = threading.Lock()
AUTO_STREAM_CACHE = {}
AUTO_STREAM_LOCK = threading.Lock()
MEM_CACHE = {}
MEM_CACHE_LOCK = threading.Lock()
IPC_SOCK_PATH = r"\\.\pipe\kino_mpv" if sys.platform == "win32" else "/tmp/kino_mpv.sock"
WINDOW_ACTION_CALLBACK = None
GET_WINDOW_GEOMETRY = None

# Migration automatique douce de l'ancien fichier JSON vers SQLite au démarrage
kino_db.migrate_from_json(CONFIG_FILE)


def cached_get(key, ttl_sec, fetch_fn):
    """Cache hybride L1 (RAM) + L2 (SQLite persistant entre redémarrages)."""
    now = time.time()
    with MEM_CACHE_LOCK:
        hit = MEM_CACHE.get(key)
        if hit and (now - hit["ts"] < ttl_sec):
            return hit["val"]

    # Niveau 2 : SQLite persistant
    db_hit = kino_db.db_cache_get(key)
    if db_hit is not None:
        with MEM_CACHE_LOCK:
            MEM_CACHE[key] = {"val": db_hit, "ts": now}
        return db_hit

    val = fetch_fn()
    if val is not None:
        with MEM_CACHE_LOCK:
            MEM_CACHE[key] = {"val": val, "ts": now}
        kino_db.db_cache_set(key, val, ttl_sec=ttl_sec)
    return val


DEBRID_PROVIDERS = {
    "realdebrid": {
        "name": "Real-Debrid",
        "short": "RD",
        "badge": "RD+",
        "torrentio_key": "realdebrid",
        "token_url": "https://real-debrid.com/apitoken",
    },
    "alldebrid": {
        "name": "AllDebrid",
        "short": "AD",
        "badge": "AD+",
        "torrentio_key": "alldebrid",
        "token_url": "https://alldebrid.fr/apikeys/",
    },
    "torbox": {
        "name": "TorBox",
        "short": "TB",
        "badge": "TB+",
        "torrentio_key": "torbox",
        "token_url": "https://torbox.app/settings",
    },
    "debridlink": {
        "name": "Debrid-Link",
        "short": "DL",
        "badge": "DL+",
        "torrentio_key": "debridlink",
        "token_url": "https://debrid-link.fr/webapp/apikey",
    },
    "premiumize": {
        "name": "Premiumize",
        "short": "PM",
        "badge": "PM+",
        "torrentio_key": "premiumize",
        "token_url": "https://www.premiumize.me/account",
    },
    "megadebrid": {
        "name": "Mega-Debrid",
        "short": "MD",
        "badge": "MD+",
        "torrentio_key": "",
        "token_url": "https://www.mega-debrid.eu/index.php?page=account",
    },
}

INSTANT_BADGES = ("RD+", "AD+", "TB+", "DL+", "PM+", "OC+", "ED+", "MD+")


def load_config():
    cfg = {
        "debrid_provider": "realdebrid",
        "provider_tokens": {},
        "rd_token": "",
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "player_mode": "kino",
        "pref_lang": "vf",
        "pref_quality": "4k",
        "hdr_mode": "sdr_pref",
        "audio_mode": "voice_boost",
        "rd_retention_days": 0,
    }
    # Support ancien fichier de config si existant
    legacy = Path.home() / ".rd_cli_config.json"
    for path in (legacy, CONFIG_FILE):
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    if not isinstance(cfg.get("provider_tokens"), dict):
        cfg["provider_tokens"] = {}
    if cfg.get("debrid_provider") not in DEBRID_PROVIDERS:
        cfg["debrid_provider"] = "realdebrid"
    # Migration / fallback du token Real-Debrid historique
    raw_rd = cfg.get("rd_token", "") or os.environ.get("RD_API_TOKEN", "")
    if raw_rd and not cfg["provider_tokens"].get("realdebrid"):
        cfg["provider_tokens"]["realdebrid"] = raw_rd

    active_prov = cfg["debrid_provider"]
    cfg["rd_token"] = cfg["provider_tokens"].get(active_prov, "")

    # Listes persistantes gérées par SQLite haute performance
    cfg["watchlist"] = kino_db.db_get_watchlist()
    cfg["history"] = kino_db.db_get_history()
    return cfg


def save_config(new_data):
    cfg = load_config()
    prov_tokens = dict(cfg.get("provider_tokens") or {})
    target_prov = new_data.get("debrid_provider") or cfg.get("debrid_provider", "realdebrid")
    if target_prov not in DEBRID_PROVIDERS:
        target_prov = "realdebrid"

    if "rd_token" in new_data:
        tok_val = (new_data.pop("rd_token") or "").strip()
        if tok_val:
            prov_tokens[target_prov] = tok_val

    # Si watchlist ou history sont envoyés, synchroniser dans SQLite
    if "watchlist" in new_data:
        wl = new_data.get("watchlist")
        if isinstance(wl, list):
            for it in wl:
                if isinstance(it, dict):
                    kino_db.db_toggle_watchlist(it)
    if "history" in new_data:
        hist = new_data.get("history")
        if isinstance(hist, list):
            for entry in hist:
                if isinstance(entry, dict):
                    kino_db.db_record_history(entry)

    cfg.update(new_data)
    cfg["debrid_provider"] = target_prov
    cfg["provider_tokens"] = prov_tokens
    cfg["rd_token"] = prov_tokens.get(target_prov, "")
    # Exclure watchlist et history du JSON pour garder le fichier léger et ultra-rapide
    file_cfg = dict(cfg)
    file_cfg.pop("watchlist", None)
    file_cfg.pop("history", None)
    CONFIG_FILE.write_text(json.dumps(file_cfg, indent=2), encoding="utf-8")
    cfg["watchlist"] = kino_db.db_get_watchlist()
    cfg["history"] = kino_db.db_get_history()
    return cfg


def http_json(url, method="GET", data=None, json_data=None, headers=None, timeout=20):
    req_headers = dict(HEADERS)
    if headers:
        req_headers.update(headers)
    encoded_data = None
    if json_data is not None:
        encoded_data = json.dumps(json_data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    elif data is not None:
        encoded_data = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error") or err_json.get("detail") or err_body
        except Exception:
            msg = err_body or str(e)
        raise RuntimeError(f"Erreur API ({e.code}): {msg}") from e


def format_size(num_bytes):
    if not num_bytes:
        return "0 B"
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


# ==========================================
# RECHERCHE CATALOGUE, MÉTADONNÉES & LISTES
# ==========================================
GENRE_FR_MAP = {
    "Action": "Action",
    "Adventure": "Aventure",
    "Animation": "Animation",
    "Biography": "Biopic",
    "Comedy": "Comédie",
    "Crime": "Policier",
    "Documentary": "Documentaire",
    "Drama": "Drame",
    "Family": "Famille",
    "Fantasy": "Fantastique",
    "History": "Histoire",
    "Horror": "Horreur",
    "Mystery": "Mystère",
    "Romance": "Romance",
    "Sci-Fi": "Science-Fiction",
    "Thriller": "Thriller",
    "War": "Guerre",
    "Western": "Western",
}


def translate_text_fr(text):
    text = (text or "").strip()
    if not text:
        return ""
    cache_key = f"tr_fr:{hash(text)}"

    def _do():
        try:
            u = (
                "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=fr&dt=t&q="
                + urllib.parse.quote(text)
            )
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                out = "".join(seg[0] for seg in (res[0] or []) if seg and seg[0]).strip()
                if out:
                    return out
        except Exception:
            pass
        try:
            u2 = "https://api.mymemory.translated.net/get?langpair=en|fr&q=" + urllib.parse.quote(text[:480])
            req2 = urllib.request.Request(u2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=3) as resp2:
                d2 = json.loads(resp2.read().decode("utf-8"))
                out2 = ((d2.get("responseData") or {}).get("translatedText") or "").strip()
                if out2 and "MYMEMORY WARNING" not in out2.upper():
                    return out2
        except Exception:
            pass
        return text

    return cached_get(cache_key, 86400, _do)


# Panthéon des Classiques du Cinéma ("Les films à voir au moins une fois dans sa vie")
KINO_CLASSICS_RAW = [
    ("tt0111161", "The Shawshank Redemption (Les Évadés)", "1994", "9.3", ["Drama"], "Condamné à perpétuité à la prison de Shawshank, le banquier Andy Dufresne se lie d'amitié avec Red et prépare patiemment sa rédemption."),
    ("tt0068646", "The Godfather (Le Parrain)", "1972", "9.2", ["Crime", "Drama"], "Le patriarche vieillissant d'une dynastie mafieuse new-yorkaise transmet l'empire clandestin à son fils cadet réticent, Michael Corleone."),
    ("tt0468569", "The Dark Knight", "2008", "9.0", ["Action", "Crime", "Drama", "Thriller"], "Batman, le lieutenant Gordon et le procureur Harvey Dent affrontent le Joker, un génie criminel anarchiste qui plonge Gotham dans le chaos."),
    ("tt0071562", "The Godfather Part II (Le Parrain 2)", "1974", "9.0", ["Crime", "Drama"], "La jeunesse de Vito Corleone à New York dans les années 1920 en parallèle de l'expansion impitoyable de l'empire de son fils Michael."),
    ("tt0050083", "12 Angry Men (Douze Hommes en colère)", "1957", "9.0", ["Crime", "Drama"], "Un juré solitaire tente de convaincre les onze autres membres du jury de reconsidérer leur verdict de culpabilité dans un procès pour meurtre."),
    ("tt0108052", "Schindler's List (La Liste de Schindler)", "1993", "9.0", ["Drama", "War"], "En Pologne occupée, l'industriel allemand Oskar Schindler sauve plus d'un millier de réfugiés juifs en les employant dans son usine."),
    ("tt0167260", "The Lord of the Rings: The Return of the King", "2003", "9.0", ["Action", "Adventure", "Drama", "Fantasy"], "Gandalf et Aragorn mènent le Monde des Hommes contre l'armée de Sauron tandis que Frodon et Sam approchent de la Montagne du Destin."),
    ("tt0110912", "Pulp Fiction", "1994", "8.9", ["Crime", "Drama"], "Les destins croisés de deux tueurs à gages philosophes, d'un boxeur en fuite et de la femme d'un caïd à Los Angeles."),
    ("tt0120737", "The Lord of the Rings: The Fellowship of the Ring", "2001", "8.9", ["Action", "Adventure", "Drama", "Fantasy"], "Un jeune Hobbit hérite d'un anneau maléfique et s'engage avec une communauté de compagnons pour le détruire au cœur du Mordor."),
    ("tt0060196", "The Good, the Bad and the Ugly (Le Bon, la Brute et le Truand)", "1966", "8.8", ["Adventure", "Western"], "Trois pistoleros rivaux s'affrontent en pleine guerre de Sécession pour mettre la main sur un trésor d'or confédéré enfoui dans un cimetière."),
    ("tt0137523", "Fight Club", "1999", "8.8", ["Drama", "Thriller"], "Un employé insomniaque désabusé et un vendeur de savon charismatique fondent un club de combat clandestin qui échappe à tout contrôle."),
    ("tt0109830", "Forrest Gump", "1994", "8.8", ["Comedy", "Drama"], "Plusieurs décennies d'histoire américaine vécues à travers le regard candide de Forrest Gump, prêt à tout pour retrouver son amour d'enfance."),
    ("tt1375666", "Inception", "2010", "8.8", ["Action", "Adventure", "Sci-Fi", "Thriller"], "Un voleur spécialisé dans l'extraction de secrets au cœur du subconscient tente l'opération inverse : implanter une idée dans l'esprit d'un héritier."),
    ("tt0167261", "The Lord of the Rings: The Two Towers", "2002", "8.8", ["Action", "Adventure", "Drama", "Fantasy"], "Alors que Frodon et Sam poursuivent leur route vers le Mordor guidés par Gollum, la Communauté divisée se prépare au siège du Gouffre de Helm."),
    ("tt0080684", "Star Wars: Episode V - The Empire Strikes Back", "1980", "8.7", ["Action", "Adventure", "Fantasy", "Sci-Fi"], "Traqués par Dark Vador à travers la galaxie, les Rebelles se dispersent tandis que Luke Skywalker suit l'enseignement du maître Jedi Yoda."),
    ("tt0133093", "The Matrix", "1999", "8.7", ["Action", "Sci-Fi"], "Un pirate informatique découvre que la réalité n'est qu'une simulation numérique créée par des machines et rejoint la rébellion humaine."),
    ("tt0099685", "Goodfellas (Les Affranchis)", "1990", "8.7", ["Crime", "Drama"], "L'ascension et la chute vertigineuse d'Henry Hill et de ses complices au sein de la mafia italo-américaine de New York sur trois décennies."),
    ("tt0073486", "One Flew Over the Cuckoo's Nest (Vol au-dessus d'un nid de coucou)", "1975", "8.7", ["Drama"], "Pour échapper à la prison, un rebelle charismatique simule la folie et bouleverse l'ordre tyrannique de l'infirmière Ratched dans un hôpital psychiatrique."),
    ("tt0816692", "Interstellar", "2014", "8.7", ["Adventure", "Drama", "Sci-Fi"], "Alors que la Terre se meurt, une équipe d'explorateurs franchit un trou de ver spatial à la recherche d'une nouvelle planète habitable pour l'humanité."),
    ("tt0114369", "Se7en", "1995", "8.6", ["Crime", "Drama", "Thriller"], "Deux inspecteurs, un vétéran désillusionné et une jeune recrue, traquent un tueur en série méthodique qui s'inspire des sept péchés capitaux."),
    ("tt0047478", "Seven Samurai (Les Sept Samouraïs)", "1954", "8.6", ["Action", "Drama"], "Dans le Japon féodal du XVIe siècle, un village de paysans engage sept samouraïs sans maître pour les défendre contre une horde de bandits."),
    ("tt0102926", "The Silence of the Lambs (Le Silence des agneaux)", "1991", "8.6", "Crime,Drama,Thriller".split(","), "Une jeune enquêtrice du FBI sollicite l'aide du brillant mais redoutable psychiatre cannibale Hannibal Lecter pour arrêter un autre tueur en série."),
    ("tt0317248", "City of God (La Cité de Dieu)", "2002", "8.6", ["Crime", "Drama"], "Dans les favelas de Rio de Janeiro des années 1960 aux années 1980, deux garçons choisissent des voies opposées entre photographie et guerre des gangs."),
    ("tt0120815", "Saving Private Ryan (Il faut sauver le soldat Ryan)", "1998", "8.6", ["Drama", "War"], "Après le débarquement de Normandie, une escouade américaine s'enfonce derrière les lignes ennemies pour ramener vivant le dernier frère survivant d'une famille."),
    ("tt0245429", "Spirited Away (Le Voyage de Chihiro)", "2001", "8.6", ["Adventure", "Animation", "Fantasy"], "Égarée dans un monde peuplé d'esprits et de dieux japonais, la jeune Chihiro doit travailler dans un établissement de bains magique pour sauver ses parents."),
    ("tt0120689", "The Green Mile (La Ligne verte)", "1999", "8.6", ["Crime", "Drama", "Fantasy"], "Dans le couloir de la mort d'un pénitencier de Louisiane en 1935, des gardiens découvrent qu'un colosse accusé de meurtre possède un don miraculeux."),
    ("tt0038650", "It's a Wonderful Life (La vie est belle)", "1946", "8.6", ["Drama", "Fantasy"], "Le soir de Noël, un ange gardien montre à un homme d'affaires désespéré à quoi ressemblerait sa petite ville s'il n'avait jamais existé."),
    ("tt6751668", "Parasite", "2019", "8.5", ["Comedy", "Drama", "Thriller"], "Toute une famille sud-coréenne sans emploi s'immisce peu à peu par la ruse au service d'une richissime famille de Séoul, jusqu'au point de bascule."),
    ("tt0110413", "Léon", "1994", "8.5", ["Action", "Crime", "Drama", "Thriller"], "Un tueur à gages solitaire recueille à contrecœur Mathilda, une fillette de douze ans dont la famille vient d'être massacrée par des policiers corrompus."),
    ("tt0056058", "Harakiri (Seppuku)", "1962", "8.6", ["Action", "Drama"], "Un rōnin âgé se présente au château d'un clan féodal pour demander l'autorisation d'accomplir le seppuku, mais révèle peu à peu une implacable vengeance."),
    ("tt0253474", "The Pianist (Le Pianiste)", "2002", "8.5", ["Drama", "War"], "Le combat pour la survie de Władysław Szpilman, célèbre pianiste juif polonais, au cœur des ruines du ghetto de Varsovie pendant la Seconde Guerre mondiale."),
    ("tt0103064", "Terminator 2: Judgment Day", "1991", "8.6", ["Action", "Sci-Fi", "Thriller"], "Un cyborg reprogrammé est envoyé du futur pour protéger le jeune John Connor contre un prototype de métal liquide quasi indestructible."),
    ("tt0088763", "Back to the Future (Retour vers le futur)", "1985", "8.5", ["Adventure", "Comedy", "Sci-Fi"], "Propulsé accidentellement en 1955 à bord d'une DeLorean modifiée par le Dr Brown, Marty McFly doit faire tomber ses propres parents amoureux."),
    ("tt2582802", "Whiplash", "2014", "8.5", ["Drama"], "Un jeune batteur de jazz prodige intègre un conservatoire d'élite où un chef d'orchestre tyrannique le pousse jusqu'à la limite de l'obsession."),
    ("tt0172495", "Gladiator", "2000", "8.5", ["Action", "Adventure", "Drama"], "Trahit par l'empereur Commode qui a fait assassiner sa famille, le général romain Maximus revient à Rome comme gladiateur pour accomplir sa vengeance."),
    ("tt0407887", "The Departed (Les Infiltrés)", "2006", "8.5", ["Crime", "Drama", "Thriller"], "À Boston, un jeune policier infiltre le gang d'un parrain irlandais tandis qu'une taupe du syndicat du crime grimpe les échelons de la police d'État."),
    ("tt0482571", "The Prestige (Le Prestige)", "2006", "8.5", ["Drama", "Sci-Fi", "Thriller"], "Dans le Londres de la fin du XIXe siècle, deux magiciens rivaux se livrent une guerre obsessionnelle et tragique pour créer l'illusion ultime."),
    ("tt0114814", "The Usual Suspects", "1995", "8.5", ["Crime", "Drama", "Thriller"], "Seul survivant d'un massacre sur un cargo en Californie, un petit escroc infirme raconte l'emprise du mystérieux criminel Keyser Söze."),
    ("tt0120586", "American History X", "1998", "8.5", ["Crime", "Drama"], "Sorti de prison après avoir abjurer son passé néo-nazi, Derek tente d'empêcher son jeune frère de sombrer dans la même spirale de haine."),
    ("tt0054215", "Psycho (Psychose)", "1960", "8.5", ["Horror", "Thriller"], "En fuite avec une somme d'argent volée, une jeune secrétaire s'arrête pour la nuit au motel isolé tenu par le troublant Norman Bates."),
    ("tt0034583", "Casablanca", "1942", "8.5", ["Drama", "War"], "En 1941 à Casablanca, le propriétaire cynique d'un cabaret doit choisir entre son amour passé pour Ilsa et l'évasion du mari résistant de celle-ci."),
    ("tt0064116", "Once Upon a Time in the West (Il était une fois dans l'Ouest)", "1968", "8.5", ["Western"], "Un mystérieux joueur d'harmonica s'allie à un hors-la-loi pour protéger une veuve contre un tueur sans pitié à la solde du chemin de fer."),
    ("tt0095327", "Grave of the Fireflies (Le Tombeau des lucioles)", "1988", "8.5", ["Animation", "Drama", "War"], "Dans le Japon dévasté de l'été 1945, un adolescent et sa petite sœur de quatre ans luttent seuls pour survivre au milieu des bombardements."),
    ("tt0095765", "Cinema Paradiso", "1988", "8.5", ["Drama"], "Un réalisateur renommé se remémore son enfance dans un village sicilien d'après-guerre et son amitié fondatrice avec le projectionniste Alfredo."),
    ("tt0078748", "Alien", "1979", "8.5", ["Horror", "Sci-Fi"], "L'équipage d'un cargo spatial commercial répond à un signal de détresse sur une lune inconnue et ramène à bord un organisme prédateur mortel."),
    ("tt0047396", "Rear Window (Fenêtre sur cour)", "1954", "8.5", ["Drama", "Thriller"], "Immobilisé dans son appartement avec une jambe plâtrée, un photographe observe ses voisins aux jumelles et se persuade que l'un d'eux a commis un meurtre."),
    ("tt0078788", "Apocalypse Now", "1979", "8.4", ["Drama", "War"], "Pendant la guerre du Vietnam, le capitaine Willard remonte un fleuve jusqu'au Cambodge pour éliminer le colonel Kurtz, devenu un demi-dieu renégat."),
    ("tt0209144", "Memento", "2000", "8.4", ["Crime", "Thriller"], "Atteint d'une perte de mémoire immédiate, un homme utilise des photos Polaroid et des tatouages sur son corps pour traquer l'assassin de sa femme."),
    ("tt1853728", "Django Unchained", "2012", "8.5", ["Drama", "Western"], "Avec l'aide d'un chasseur de primes allemand, un esclave affranchi traverse le Sud américain pour délivrer sa femme d'un planteur sadique."),
    ("tt0081505", "The Shining", "1980", "8.4", ["Drama", "Horror"], "Isolé par le blizzard dans un immense hôtel des Rocheuses dont il est le gardien d'hiver, un écrivain sombre dans une folie meurtrière surnaturelle."),
    ("tt0910970", "WALL·E", "2008", "8.4", ["Adventure", "Animation", "Sci-Fi"], "Sur une Terre désertée et couverte de déchets, un petit robot compacteur solitaire tombe amoureux d'une sonde envoyée chercher une trace de vie."),
    ("tt0043014", "Sunset Boulevard", "1950", "8.4", ["Drama"], "Un scénariste fauché devient le prisonnier doré d'une ancienne gloire du cinéma muet recluse dans son manoir et persuadée de son retour triomphal."),
    ("tt0050825", "Paths of Glory (Les Sentiers de la gloire)", "1957", "8.4", ["Drama", "War"], "En 1916 dans les tranchées françaises, un colonel défend en cour martiale trois soldats accusés de lâcheté après l'échec d'un assaut suicide."),
    ("tt0364569", "Oldboy", "2003", "8.3", ["Action", "Drama", "Thriller"], "Séquestré pendant quinze ans dans une chambre sans savoir pourquoi, Oh Dae-su est brusquement relâché avec cinq jours pour découvrir la vérité."),
    ("tt0082971", "Raiders of the Lost Ark (Les Aventuriers de l'arche perdue)", "1981", "8.4", ["Action", "Adventure"], "En 1936, l'archéologue aventurier Indiana Jones parcourt le globe pour retrouver l'Arche d'Alliance avant l'armée nazie."),
    ("tt0119698", "Princess Mononoke (Princesse Mononoké)", "1997", "8.3", ["Action", "Adventure", "Animation", "Fantasy"], "Frappé d'une malédiction mortelle, le prince Ashitaka se retrouve au cœur d'une guerre entre les dieux de la forêt et une colonie minière humaine."),
    ("tt0090605", "Aliens", "1986", "8.4", ["Action", "Adventure", "Sci-Fi", "Thriller"], "Cinquante-sept ans après avoir survécu au Nostromo, Ellen Ripley retourne sur la planète LV-426 accompagnée d'une unité de Marines coloniaux."),
    ("tt0057012", "Dr. Strangelove (Docteur Folamour)", "1964", "8.4", ["Comedy", "War"], "Un général américain paranoïaque ordonne une attaque nucléaire sur l'URSS, déclenchant une course contre la montre absurde au Pentagone."),
    ("tt0087843", "Once Upon a Time in America (Il était une fois en Amérique)", "1984", "8.3", ["Crime", "Drama"], "Fresque mélancolique sur cinquante ans d'amitié, d'ambition et de trahison au sein d'un groupe de gangsters juifs du Lower East Side de New York."),
    ("tt0091251", "Come and See (Requiem pour un massacre)", "1985", "8.4", ["Drama", "War"], "En Biélorussie occupée en 1943, un adolescent rejoint les partisans soviétiques et traverse l'horreur absolue des représailles nazies."),
    ("tt4633694", "Spider-Man: Into the Spider-Verse", "2018", "8.4", ["Action", "Adventure", "Animation", "Sci-Fi"], "L'adolescent de Brooklyn Miles Morales devient le nouveau Spider-Man et fait équipe avec des homologues issus de dimensions parallèles."),
    ("tt0075314", "Taxi Driver", "1976", "8.2", ["Crime", "Drama"], "Vétéran du Vietnam insomniaque conduisant un taxi de nuit à New York, Travis Bickle s'enfonce dans l'obsession de purifier une ville qu'il juge corrompue."),
    ("tt0062622", "2001: A Space Odyssey (2001, l'Odyssée de l'espace)", "1968", "8.3", ["Adventure", "Sci-Fi"], "Après la découverte d'un monolithe noir sur la Lune, une mission spatiale vers Jupiter tourne au duel silencieux avec l'ordinateur de bord HAL 9000."),
    ("tt0361748", "Inglourious Basterds", "2009", "8.4", ["Adventure", "Drama", "War"], "Dans la France occupée, le projet de vengeance d'une jeune propriétaire de cinéma juive croise le commando de chasseurs de nazis d'Aldo Raine."),
    ("tt0086879", "Amadeus", "1984", "8.4", ["Drama"], "À Vienne au XVIIIe siècle, le compositeur de la cour Antonio Salieri est dévoré de jalousie face au génie divin du jeune Wolfgang Amadeus Mozart."),
    ("tt0113277", "Heat", "1995", "8.3", ["Action", "Crime", "Drama", "Thriller"], "À Los Angeles, un braqueur de haut vol méticuleux et un lieutenant de police obsessionnel se livrent un duel implacable d'une rare intensité."),
    ("tt0086250", "Scarface", "1983", "8.3", ["Crime", "Drama"], "Réfugié cubain arrivé sans rien à Miami en 1980, Tony Montana bâtit par la violence un empire de la cocaïne avant d'être consumé par sa paranoïa."),
    ("tt0105236", "Reservoir Dogs", "1992", "8.3", ["Crime", "Thriller"], "Après qu'un braquage de diamants a viré au bain de sang, les survivants se retranchent dans un entrepôt et cherchent lequel d'entre eux est un indicateur."),
    ("tt0119217", "Good Will Hunting (Will Hunting)", "1997", "8.3", ["Drama"], "Un jeune concierge du MIT doté d'un génie mathématique hors norme mais autodestructeur noue un lien salvateur avec un psychologue endeuillé."),
    ("tt0180093", "Requiem for a Dream", "2000", "8.3", ["Drama"], "Quatre habitants de Coney Island voient leurs rêves d'une vie meilleure se briser à mesure qu'ils plongent dans de terribles addictions."),
    ("tt0338013", "Eternal Sunshine of the Spotless Mind", "2004", "8.3", ["Drama", "Sci-Fi"], "Découvrant que son ex-compagne a fait effacer médicalement tous leurs souvenirs communs, Joel décide de subir la même procédure mais change d'avis en plein rêve."),
    ("tt0052357", "Vertigo (Sueurs froides)", "1958", "8.3", ["Drama", "Thriller"], "Un ancien inspecteur de San Francisco souffrant d'acrophobie est chargé de filer l'épouse suicidaire d'un ami et sombre dans une fascination vertigineuse."),
    ("tt0033467", "Citizen Kane", "1941", "8.3", ["Drama"], "À la mort d'un magnat de la presse, un journaliste enquête sur le sens de son dernier mot, « Rosebud », révélant l'ascension et la solitude d'un géant."),
    ("tt0056172", "Lawrence of Arabia (Lawrence d'Arabie)", "1962", "8.3", ["Adventure", "Drama", "War"], "L'épopée flamboyante de l'officier britannique T.E. Lawrence qui unifia les tribus arabes du désert contre l'Empire ottoman durant la Grande Guerre."),
    ("tt0093058", "Full Metal Jacket", "1987", "8.3", ["Drama", "War"], "Du dressage déshumanisant d'un camp d'entraînement des Marines jusqu'aux combats urbains sanglants de l'offensive du Têt à Hué."),
    ("tt0066921", "A Clockwork Orange (Orange mécanique)", "1971", "8.3", ["Crime", "Sci-Fi"], "Dans un futur dystopique, le jeune chef de gang ultra-violent Alex DeLarge accepte un traitement expérimental de conditionnement mental pour sortir de prison."),
    ("tt0070735", "The Sting (L'Arnaque)", "1973", "8.3", ["Comedy", "Crime", "Drama"], "À Chicago dans les années 1930, deux escrocs de génie montent l'arnaque du siècle pour ruiner un puissant chef de la pègre."),
    ("tt0211915", "Amélie (Le Fabuleux Destin d'Amélie Poulain)", "2001", "8.3", ["Comedy", "Drama"], "Serveuse rêveuse à Montmartre, Amélie décide de réparer secrètement la vie des gens qui l'entourent tout en cherchant sa propre place dans le monde."),
    ("tt1255953", "Incendies", "2010", "8.3", ["Drama"], "À la lecture du testament de leur mère, des jumeaux partent au Moyen-Orient sur les traces d'un passé familial bouleversant."),
    ("tt0112641", "Casino", "1995", "8.2", ["Crime", "Drama"], "Dans le Las Vegas des années 1970, l'empire étincelant d'un directeur de casino lié à la mafia s'effondre sous le poids de la cupidité et de la trahison."),
    ("tt0083658", "Blade Runner", "1982", "8.1", ["Action", "Drama", "Sci-Fi", "Thriller"], "Dans un Los Angeles pluvieux et cybernétique en 2019, un agent spécial traque quatre androïdes rebelles revenus sur Terre chercher leur créateur."),
    ("tt0071315", "Chinatown", "1974", "8.1", ["Crime", "Drama", "Thriller"], "Engagé pour une banale affaire d'adultère dans le Los Angeles des années 1930, le détective privé J.J. Gittes met au jour un complot autour de l'eau."),
    ("tt0477348", "No Country for Old Men", "2007", "8.2", ["Crime", "Drama", "Thriller"], "Au Texas en 1980, un chasseur tombe sur deux millions de dollars issus d'un trafic qui a mal tourné, déclenchant la traque implacable du tueur Anton Chigurh."),
    ("tt0469494", "There Will Be Blood", "2007", "8.2", ["Drama"], "L'ascension impitoyable d'un prospecteur de pétrole dévoré par l'ambition en Californie au tournant du XXe siècle face à un jeune prédicateur."),
    ("tt0120382", "The Truman Show", "1998", "8.2", ["Comedy", "Drama"], "Un courtier en assurances découvre peu à peu que toute son existence depuis sa naissance est un feuilleton télévisé diffusé en direct 24h/24."),
    ("tt0118715", "The Big Lebowski", "1998", "8.1", ["Comedy", "Crime"], "Confondu avec un millionnaire portant le même nom, « Le Duc », chômeur amateur de bowling à Los Angeles, est entraîné dans une affaire d'enlèvement loufoque."),
    ("tt0116282", "Fargo", "1996", "8.1", ["Crime", "Drama", "Thriller"], "Dans le Minnesota enneigé, le projet maladroit d'un vendeur de voitures pour faire enlever sa propre femme tourne au carnage sous l'œil d'une policière enceinte."),
    ("tt0084787", "The Thing", "1982", "8.2", ["Horror", "Sci-Fi", "Thriller"], "Dans une station de recherche isolée en Antarctique, douze scientifiques affrontent une créature extraterrestre capable d'imiter parfaitement ses victimes."),
    ("tt0113247", "La Haine", "1995", "8.1", ["Crime", "Drama"], "Vingt-quatre heures décisives dans la vie de Vinz, Saïd et Hubert, trois jeunes d'une cité de banlieue parisienne au lendemain d'une nuit d'émeutes."),
    ("tt0166924", "Mulholland Drive", "2001", "7.9", ["Drama", "Thriller"], "Après un accident sur les hauteurs de Los Angeles, une femme amnésique et une jeune actrice pleine d'espoir s'aventurent dans un labyrinthe onirique à Hollywood."),
    ("tt0118694", "In the Mood for Love", "2000", "8.1", ["Drama"], "À Hong Kong en 1962, deux voisins d'appartement découvrent que leurs conjoints respectifs ont une liaison et nouent un lien d'une pudeur déchirante."),
    ("tt0353969", "Memories of Murder", "2003", "8.1", ["Crime", "Drama", "Thriller"], "En 1986 dans une province sud-coréenne, deux inspecteurs aux méthodes opposées s'épuisent à traquer le premier tueur en série du pays."),
    ("tt1130884", "Shutter Island", "2010", "8.2", ["Drama", "Thriller"], "En 1954, le marshal Teddy Daniels enquête sur la disparition d'une patiente dans un hôpital psychiatrique pénitentiaire situé sur une île battue par les vents."),
    ("tt0993846", "The Wolf of Wall Street (Le Loup de Wall Street)", "2013", "8.2", ["Comedy", "Crime", "Drama"], "L'ascension euphorique et la chute de Jordan Belfort, courtier new-yorkais qui bâtit une fortune colossale par la fraude et la démesure dans les années 1990."),
    ("tt1392214", "Prisoners", "2013", "8.2", ["Crime", "Drama", "Thriller"], "Lorsque sa petite fille disparaît avec une amie à Thanksgiving, un père désespéré décide de faire justice lui-même pendant que l'inspecteur Loki mène l'enquête."),
    ("tt0107290", "Jurassic Park", "1993", "8.2", ["Action", "Adventure", "Sci-Fi"], "Une panne de sécurité dans un parc insulaire peuplé de dinosaures clonés transforme une visite d'inspection en lutte pour la survie."),
    ("tt0075148", "Rocky", "1976", "8.1", ["Drama"], "Un modeste boxeur de Philadelphie se voit offrir la chance inespérée d'affronter le champion du monde poids lourds Apollo Creed."),
    ("tt0081398", "Raging Bull", "1980", "8.1", ["Drama"], "Le portrait en noir et blanc de Jake LaMotta, champion de boxe poids moyen dont la rage sur le ring détruit peu à peu sa vie familiale."),
    ("tt0072684", "Barry Lyndon", "1975", "8.1", ["Adventure", "Drama", "War"], "Au XVIIIe siècle, un jeune Irlandais sans fortune use de duels, d'intrigues et d'un mariage intéressé pour se hisser parmi la noblesse anglaise."),
    ("tt0079944", "Stalker", "1979", "8.1", ["Drama", "Sci-Fi"], "Un guide clandestin conduit un écrivain et un scientifique au cœur de « La Zone », un territoire interdit où les lois de la physique semblent abolies."),
    ("tt0206634", "Children of Men (Les Fils de l'homme)", "2006", "7.9", ["Action", "Drama", "Sci-Fi", "Thriller"], "En 2027, dans un monde frappé par dix-huit ans d'infertilité totale, un ancien militant accepte d'escorter une jeune femme miraculeusement enceinte."),
    ("tt0443706", "Zodiac", "2007", "7.7", ["Crime", "Drama", "Thriller"], "À San Francisco, un dessinateur de presse, un journaliste et deux inspecteurs sacrifient leurs vies à déchiffrer les énigmes du tueur du Zodiaque."),
    ("tt1285016", "The Social Network", "2010", "7.8", ["Drama"], "Par une soirée d'automne 2003 à Harvard, l'étudiant Mark Zuckerberg lance un site qui révolutionnera la communication mondiale tout en déchirant ses fondateurs."),
    ("tt0780504", "Drive", "2011", "7.8", ["Action", "Crime", "Drama", "Thriller"], "Cascadeur solitaire le jour et chauffeur de braquages la nuit à Los Angeles, un homme met sa vie en jeu pour protéger sa voisine et son fils."),
    ("tt1798709", "Her", "2013", "8.0", ["Drama", "Sci-Fi"], "Dans un Los Angeles futuriste, un écrivain solitaire en plein divorce tombe amoureux de Samantha, un système d'exploitation doté d'une intelligence sensible."),
    ("tt2267998", "Gone Girl", "2014", "8.1", ["Drama", "Thriller"], "À l'occasion de leur cinquième anniversaire de mariage, l'épouse de Nick Dunne disparaît mystérieusement et les soupçons médiatiques se referment sur lui."),
    ("tt2278388", "The Grand Budapest Hotel", "2014", "8.1", ["Adventure", "Comedy", "Crime"], "Les mésaventures rocambolesques de Gustave H, concierge légendaire d'un grand palace européen de l'entre-deux-guerres, et de son jeune groom Zero."),
    ("tt1392190", "Mad Max: Fury Road", "2015", "8.1", ["Action", "Adventure", "Sci-Fi"], "Dans un désert post-apocalyptique, Max s'allie à l'Impératrice Furiosa pour fuir un tyran et son armée motorisée à bord d'un camion-citerne blindé."),
    ("tt1856101", "Blade Runner 2049", "2017", "8.0", ["Action", "Drama", "Sci-Fi", "Thriller"], "Un jeune Blade Runner découvre un secret enfoui depuis trente ans qui le pousse à retrouver Rick Deckard, disparu depuis des décennies."),
    ("tt2543164", "Arrival (Premier Contact)", "2016", "7.9", ["Drama", "Sci-Fi", "Thriller"], "Lorsque douze vaisseaux extraterrestres apparaissent sur Terre, une linguiste est recrutée par l'armée pour déchiffrer leur langage circulaire."),
    ("tt3783958", "La La Land", "2016", "8.0", ["Comedy", "Drama"], "À Los Angeles, une actrice en devenir et un pianiste de jazz passionné tombent amoureux tout en poursuivant des rêves artistiques exigeants."),
    ("tt8613070", "Portrait de la jeune fille en feu", "2019", "8.1", ["Drama"], "En Bretagne en 1770, une peintre est chargée de réaliser en secret le portrait de mariage d'une jeune femme tout juste sortie du couvent."),
    ("tt15398776", "Oppenheimer", "2023", "8.3", ["Drama", "History"], "Le destin du physicien J. Robert Oppenheimer, directeur scientifique du projet Manhattan qui développa la première arme atomique."),
    ("tt15239678", "Dune: Deuxième Partie", "2024", "8.5", ["Action", "Adventure", "Drama", "Sci-Fi"], "Paul Atréides s'unit à Chani et aux Fremen d'Arrakis pour mener une guerre sainte contre les conspirateurs qui ont anéanti sa maison."),
    ("tt0266697", "Kill Bill: Volume 1", "2003", "8.2", ["Action", "Crime", "Thriller"], "Sortie d'un coma de quatre ans après le massacre de son mariage, « La Mariée » dresse une liste de vengeance contre ses anciens complices assassins."),
    ("tt0434409", "V for Vendetta (V pour Vendetta)", "2005", "8.2", ["Action", "Drama", "Sci-Fi", "Thriller"], "Dans une Angleterre totalitaire, un justicier masqué connu sous le nom de « V » orchestre une révolte spectaculaire contre la dictature."),
    ("tt0457430", "Pan's Labyrinth (Le Labyrinthe de Pan)", "2006", "8.2", ["Drama", "Fantasy", "War"], "En Espagne franquiste en 1944, la jeune Ofelia découvre un labyrinthe ancien où un faune lui révèle qu'elle serait la princesse d'un royaume souterrain."),
    ("tt0167404", "The Sixth Sense (Sixième Sens)", "1999", "8.2", ["Drama", "Thriller"], "Un psychologue pour enfants marqué par un échec tente d'aider Cole, un garçon de huit ans terrorisé par un lourd secret : il voit des morts."),
    ("tt0071853", "Monty Python and the Holy Grail (Sacré Graal !)", "1975", "8.2", ["Adventure", "Comedy", "Fantasy"], "Le roi Arthur et ses chevaliers de la Table Ronde se lancent dans une quête surréaliste et hilarante à la recherche du Saint Graal."),
    ("tt0073195", "Jaws (Les Dents de la mer)", "1975", "8.1", ["Adventure", "Thriller"], "Lorsqu'un grand requin blanc sème la terreur sur les plages d'une île touristique, le chef de la police, un océanographe et un chasseur prennent la mer."),
]

# Initialisation en arrière-plan du catalogue local FTS5 avec les classiques
try:
    threading.Thread(target=kino_db.db_seed_classics, args=(KINO_CLASSICS_RAW,), daemon=True).start()
except Exception:
    pass


def get_classics_catalog(genre="", sort="top"):
    genre_norm = (genre or "").strip().lower()
    items = []
    for imdb_id, name, year, rating, genres, desc in KINO_CLASSICS_RAW:
        if genre_norm and not any(g.lower() == genre_norm for g in genres):
            continue
        items.append({
            "id": imdb_id,
            "name": name,
            "type": "movie",
            "releaseInfo": year,
            "year": year,
            "imdbRating": rating,
            "genres": genres,
            "description": desc,
            "poster": f"https://images.metahub.space/poster/medium/{imdb_id}/img",
            "background": f"https://images.metahub.space/background/medium/{imdb_id}/img",
        })
    if sort == "imdbRating":
        items.sort(key=lambda m: float(m.get("imdbRating") or 0), reverse=True)
    elif sort == "recent":
        items.sort(key=lambda m: int(m.get("year") or 0), reverse=True)
    elif sort == "oldest":
        items.sort(key=lambda m: int(m.get("year") or 9999))
    return items


CINEMETA_FALLBACK_ANIMES = [
    {"id": "tt2560140", "type": "series", "name": "Attack on Titan", "year": "2013", "releaseInfo": "2013-2023", "imdbRating": "9.1", "poster": "https://images.metahub.space/poster/medium/tt2560140/img", "genres": ["Animation", "Action", "Adventure"], "description": "Dans un monde où les humains vivent enfermés dans des cités entourées de gigantesques remparts pour se protéger de créatures colossales nommées Titans, le jeune Eren Jaeger jure d'éradiquer ces prédateurs."},
    {"id": "tt12343534", "type": "series", "name": "Jujutsu Kaisen", "year": "2020", "releaseInfo": "2020-", "imdbRating": "8.5", "poster": "https://images.metahub.space/poster/medium/tt12343534/img", "genres": ["Animation", "Action", "Fantasy"], "description": "Yuji Itadori, lycéen aux aptitudes physiques exceptionnelles, avale une relique maudite de rang S pour sauver ses amis et se retrouve possédé par Ryomen Sukuna, le Roi des Fléaux."},
    {"id": "tt9335498", "type": "series", "name": "Demon Slayer: Kimetsu no Yaiba", "year": "2019", "releaseInfo": "2019-", "imdbRating": "8.6", "poster": "https://images.metahub.space/poster/medium/tt9335498/img", "genres": ["Animation", "Action", "Fantasy"], "description": "Après le massacre de sa famille par un démon et la transformation de sa jeune sœur Nezuko, Tanjiro Kamado devient pourfendeur de démons pour la délivrer de cette malédiction."},
    {"id": "tt0388629", "type": "series", "name": "One Piece", "year": "1999", "releaseInfo": "1999-", "imdbRating": "9.0", "poster": "https://images.metahub.space/poster/medium/tt0388629/img", "genres": ["Animation", "Action", "Adventure"], "description": "Monkey D. Luffy prend la mer à la recherche du trésor légendaire, le One Piece, avec l'ambition suprême de devenir le Roi des Pirates."},
    {"id": "tt22248374", "type": "series", "name": "Frieren: Beyond Journey's End", "year": "2023", "releaseInfo": "2023-", "imdbRating": "8.9", "poster": "https://images.metahub.space/poster/medium/tt22248374/img", "genres": ["Animation", "Adventure", "Drama"], "description": "Après la défaite du Roi Démon par le groupe de héros, l'elfe magicienne Frieren entame un nouveau voyage pour comprendre la valeur éphémère du temps et des liens humains."},
    {"id": "tt21209876", "type": "series", "name": "Solo Leveling", "year": "2024", "releaseInfo": "2024-", "imdbRating": "8.3", "poster": "https://images.metahub.space/poster/medium/tt21209876/img", "genres": ["Animation", "Action", "Fantasy"], "description": "Sung Jinwoo, le chasseur le plus faible du monde, reçoit la capacité unique d'évoluer sans limite via une interface de jeu invisible."},
    {"id": "tt0877057", "type": "series", "name": "Death Note", "year": "2006", "releaseInfo": "2006-2007", "imdbRating": "8.9", "poster": "https://images.metahub.space/poster/medium/tt0877057/img", "genres": ["Animation", "Crime", "Drama"], "description": "Light Yagami, brillant lycéen, trouve un carnet surnaturel permettant de tuer quiconque dont on connaît le nom et le visage."},
    {"id": "tt1370601", "type": "series", "name": "Fullmetal Alchemist: Brotherhood", "year": "2009", "releaseInfo": "2009-2010", "imdbRating": "9.1", "poster": "https://images.metahub.space/poster/medium/tt1370601/img", "genres": ["Animation", "Action", "Adventure"], "description": "Edward et Alphonse Elric parcourent le monde à la recherche de la Pierre Philosophale pour restaurer leurs corps perdus."},
    {"id": "tt2098220", "type": "series", "name": "Hunter x Hunter", "year": "2011", "releaseInfo": "2011-2014", "imdbRating": "9.0", "poster": "https://images.metahub.space/poster/medium/tt2098220/img", "genres": ["Animation", "Action", "Adventure"], "description": "Gon Freecss décide de passer le redoutable examen de Hunter dans l'espoir de retrouver son père Ging, l'un des Hunters les plus mystérieux au monde."},
    {"id": "tt13616990", "type": "series", "name": "Chainsaw Man", "year": "2022", "releaseInfo": "2022-", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt13616990/img", "genres": ["Animation", "Action", "Horror"], "description": "Denji, jeune homme criblé de dettes vivant avec son démon-tronçonneuse Pochita, fusionne avec ce dernier pour devenir Chainsaw Man."},
    {"id": "tt0434665", "type": "series", "name": "Bleach", "year": "2004", "releaseInfo": "2004-2012", "imdbRating": "8.2", "poster": "https://images.metahub.space/poster/medium/tt0434665/img", "genres": ["Animation", "Action", "Adventure"], "description": "Ichigo Kurosaki, adolescent capable de voir les esprits, devient Shinigami pour défendre les humains contre les monstres Hollows."},
    {"id": "tt0409591", "type": "series", "name": "Naruto", "year": "2002", "releaseInfo": "2002-2007", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt0409591/img", "genres": ["Animation", "Action", "Adventure"], "description": "Naruto Uzumaki, jeune ninja orphelin porteur du Démon-Renard à neuf queues, rêve de devenir Hokage pour être enfin reconnu par tous."},
    {"id": "tt0988824", "type": "series", "name": "Naruto: Shippuden", "year": "2007", "releaseInfo": "2007-2017", "imdbRating": "8.7", "poster": "https://images.metahub.space/poster/medium/tt0988824/img", "genres": ["Animation", "Action", "Adventure"], "description": "Deux ans et demi après son départ, Naruto revient à Konoha plus fort que jamais face à la menace grandissante de l'Akatsuki."},
    {"id": "tt0245429", "type": "movie", "name": "Le Voyage de Chihiro", "year": "2001", "releaseInfo": "2001", "imdbRating": "8.6", "poster": "https://images.metahub.space/poster/medium/tt0245429/img", "genres": ["Animation", "Adventure", "Family"], "description": "Chihiro, une fillette de dix ans, s'aventure dans un parc à thème abandonné qui s'avère être un monde enchanté peuplé d'esprits."},
    {"id": "tt5311514", "type": "movie", "name": "Your Name.", "year": "2016", "releaseInfo": "2016", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt5311514/img", "genres": ["Animation", "Drama", "Fantasy"], "description": "Mitsuha, lycéenne dans un village rural, et Taki, lycéen à Tokyo, découvrent qu'ils échangent mystérieusement de corps pendant leur sommeil."},
    {"id": "tt0119698", "type": "movie", "name": "Princesse Mononoké", "year": "1997", "releaseInfo": "1997", "imdbRating": "8.3", "poster": "https://images.metahub.space/poster/medium/tt0119698/img", "genres": ["Animation", "Action", "Adventure"], "description": "Frappé d'une malédiction, le jeune guerrier Ashitaka quitte son village et se retrouve pris dans une guerre sanglante entre les dieux de la forêt et les humains."}
]


def get_catalog_top(media_type="movie", genre="", skip=0, sort="top"):
    genre = (genre or "").strip()
    sort = (sort or "top").strip()
    skip = max(0, int(skip or 0))
    if media_type == "classics":
        all_classics = get_classics_catalog(genre=genre, sort=sort)
        return all_classics[skip:] if skip > 0 else all_classics
    if media_type == "anime":
        cache_key = f"catalog:anime_v2:{sort}:{genre or 'all'}:{skip}"

        def _fetch_anime():
            is_movie = genre in ("Films", "Films d'Animation", "movie", "Film")
            c_type = "movie" if is_movie else "series"
            parts = ["genre=Anime"]
            if skip > 0:
                parts.append(f"skip={skip}")
            extra = ("/" + "&".join(parts)) if parts else ""
            url = f"https://v3-cinemeta.strem.io/catalog/{c_type}/top{extra}.json"
            data = http_json(url)
            metas = data.get("metas", [])
            for m in metas:
                if not m.get("type"):
                    m["type"] = c_type
                m["is_anime"] = True

            if skip == 0 and not is_movie and (not genre or genre in ("Tous", "Tendances", "Populaires")):
                existing_ids = {m.get("id") for m in metas if m.get("id")}
                staples = [dict(a) for a in CINEMETA_FALLBACK_ANIMES if a.get("type") == "series" and a.get("id") not in existing_ids]
                metas = staples[:6] + metas

            if not is_movie and genre and genre not in ("Tous", "Tendances", "Populaires"):
                filtered = [m for m in metas if genre.lower() in [g.lower() for g in (m.get("genres") or [])]]
                fallback_matches = [m for m in CINEMETA_FALLBACK_ANIMES if genre.lower() in [g.lower() for g in (m.get("genres") or [])]]
                combined = []
                seen_f = set()
                for item in filtered + fallback_matches:
                    iid = item.get("id")
                    if iid and iid not in seen_f:
                        seen_f.add(iid)
                        combined.append(item)
                if combined:
                    metas = combined

            if not metas and skip == 0:
                if is_movie:
                    metas = [m for m in CINEMETA_FALLBACK_ANIMES if m.get("type") == "movie"]
                elif genre and genre not in ("Tous", "Tendances", "Populaires"):
                    metas = [m for m in CINEMETA_FALLBACK_ANIMES if genre.lower() in [g.lower() for g in (m.get("genres") or [])]]
                else:
                    metas = list(CINEMETA_FALLBACK_ANIMES)

            if metas:
                try:
                    threading.Thread(target=kino_db.db_index_media, args=(metas,), daemon=True).start()
                except Exception:
                    pass

            if sort == "imdbRating":
                metas = sorted(
                    metas,
                    key=lambda m: float(m.get("imdbRating") or 0) if str(m.get("imdbRating") or "").replace(".", "", 1).isdigit() else 0.0,
                    reverse=True,
                )
            elif sort == "recent":
                metas = sorted(
                    metas,
                    key=lambda m: str(m.get("releaseInfo") or m.get("year") or "0")[:4],
                    reverse=True,
                )
            elif sort == "oldest":
                metas = sorted(
                    metas,
                    key=lambda m: int(str(m.get("releaseInfo") or m.get("year") or "9999")[:4]) if str(m.get("releaseInfo") or m.get("year") or "")[:4].isdigit() else 9999,
                )
            return metas

        return cached_get(cache_key, 900, _fetch_anime)

    if genre == "imdbRating":
        sort = "imdbRating"
        genre = ""
    catalog_id = "top"
    cache_key = f"catalog:{media_type}:{sort}:{genre or 'all'}:{skip}"

    def _fetch():
        parts = []
        if genre:
            parts.append(f"genre={urllib.parse.quote(genre)}")
        if skip > 0:
            parts.append(f"skip={skip}")
        extra = ("/" + "&".join(parts)) if parts else ""
        url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/{catalog_id}{extra}.json"
        data = http_json(url)
        metas = data.get("metas", [])
        if metas:
            try:
                threading.Thread(target=kino_db.db_index_media, args=(metas,), daemon=True).start()
            except Exception:
                pass
        if sort == "imdbRating":
            metas = sorted(
                metas,
                key=lambda m: float(m.get("imdbRating") or 0) if str(m.get("imdbRating") or "").replace(".", "", 1).isdigit() else 0.0,
                reverse=True,
            )
        elif sort == "recent":
            metas = sorted(
                metas,
                key=lambda m: str(m.get("releaseInfo") or m.get("year") or "0")[:4],
                reverse=True,
            )
        elif sort == "oldest":
            metas = sorted(
                metas,
                key=lambda m: int(str(m.get("releaseInfo") or m.get("year") or "9999")[:4]) if str(m.get("releaseInfo") or m.get("year") or "")[:4].isdigit() else 9999,
            )
        return metas

    return cached_get(cache_key, 900, _fetch)


def search_cinemeta(query, media_type="movie"):
    q_norm = (query or "").strip().lower()
    cache_key = f"search:{media_type}:{q_norm}"

    def _fetch():
        if media_type == "anime":
            s_metas = search_cinemeta(query, "series") or []
            m_metas = search_cinemeta(query, "movie") or []
            seen = set()
            combined = []
            for m in s_metas + m_metas:
                mid = m.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    combined.append(m)
            return combined

        encoded = urllib.parse.quote(query)
        url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
        data = http_json(url)
        metas = data.get("metas", [])
        if metas:
            try:
                threading.Thread(target=kino_db.db_index_media, args=(metas,), daemon=True).start()
            except Exception:
                pass
        return metas

    return cached_get(cache_key, 600, _fetch)


def get_media_meta(imdb_id, media_type="movie"):
    if media_type == "anime":
        s_meta = get_media_meta(imdb_id, "series")
        if s_meta and s_meta.get("name") and s_meta.get("videos"):
            s_meta["type"] = "series"
            s_meta["is_anime"] = True
            return s_meta
        m_meta = get_media_meta(imdb_id, "movie")
        if m_meta and m_meta.get("name"):
            m_meta["type"] = "movie"
            m_meta["is_anime"] = True
            return m_meta
        return s_meta or m_meta or {}

    cache_key = f"meta_fr_v2:{media_type}:{imdb_id}"

    def _fetch():
        url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
        data = http_json(url)
        meta = dict(data.get("meta") or {})
        if meta.get("description"):
            meta["description_fr"] = translate_text_fr(meta["description"])
        raw_genres = meta.get("genres") or meta.get("genre") or []
        if isinstance(raw_genres, list):
            meta["genres_fr"] = [GENRE_FR_MAP.get(g, g) for g in raw_genres]

        # Recommandations "Titres similaires" par réalisateur / casting / genres partagés
        similar = []
        seen_ids = {imdb_id}
        try:
            directors = meta.get("director") if isinstance(meta.get("director"), list) else ([meta["director"]] if meta.get("director") else [])
            cast_list = meta.get("cast") if isinstance(meta.get("cast"), list) else []
            seed_query = (directors[0] if directors else (cast_list[0] if cast_list else "")).strip()
            if seed_query:
                for cand in (search_cinemeta(seed_query, media_type) or [])[:6]:
                    cid = cand.get("id")
                    if cid and cid not in seen_ids and cand.get("poster"):
                        seen_ids.add(cid)
                        similar.append({
                            "id": cid,
                            "name": cand.get("name", ""),
                            "type": cand.get("type") or media_type,
                            "year": str(cand.get("releaseInfo") or cand.get("year") or ""),
                            "poster": cand.get("poster", ""),
                            "imdbRating": str(cand.get("imdbRating") or ""),
                        })
            if len(similar) < 6 and raw_genres and isinstance(raw_genres, list):
                primary_genre = raw_genres[0]
                genre_set = set(raw_genres)
                pool = get_catalog_top(media_type, genre=primary_genre, skip=0, sort="top") or []
                scored_pool = []
                for cand in pool:
                    cid = cand.get("id")
                    if not cid or cid in seen_ids or not cand.get("poster"):
                        continue
                    c_genres = set(cand.get("genres") or cand.get("genre") or [])
                    overlap = len(genre_set.intersection(c_genres))
                    rating_f = 0.0
                    try:
                        rating_f = float(cand.get("imdbRating") or 0)
                    except Exception:
                        pass
                    scored_pool.append(((overlap, rating_f), cand))
                scored_pool.sort(key=lambda x: x[0], reverse=True)
                for _, cand in scored_pool[: (8 - len(similar))]:
                    cid = cand.get("id")
                    seen_ids.add(cid)
                    similar.append({
                        "id": cid,
                        "name": cand.get("name", ""),
                        "type": cand.get("type") or media_type,
                        "year": str(cand.get("releaseInfo") or cand.get("year") or ""),
                        "poster": cand.get("poster", ""),
                        "imdbRating": str(cand.get("imdbRating") or ""),
                    })
        except Exception:
            pass
        meta["similar"] = similar[:8]

        # Détection du dernier épisode diffusé pour les séries (badge "Nouvel épisode diffusé")
        if media_type == "series" and isinstance(meta.get("videos"), list):
            try:
                from datetime import datetime, timezone
                today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                aired_eps = []
                for v in meta["videos"]:
                    s_num = int(v.get("season") or 0)
                    e_num = int(v.get("episode") or v.get("number") or 0)
                    rel = str(v.get("released") or "")[:10]
                    if s_num >= 1 and e_num >= 1 and len(rel) == 10 and rel <= today_iso:
                        aired_eps.append((s_num, e_num, rel, v.get("name") or v.get("title") or f"Épisode {e_num}"))
                if aired_eps:
                    aired_eps.sort(key=lambda x: (x[0], x[1]))
                    ls, le, lrel, ltitle = aired_eps[-1]
                    meta["latest_aired"] = {
                        "season": ls,
                        "episode": le,
                        "code": f"S{ls:02d}E{le:02d}",
                        "released": lrel,
                        "title": ltitle,
                    }
            except Exception:
                pass

        return meta

    return cached_get(cache_key, 1800, _fetch)


def fetch_opensubtitles(imdb_id, media_type="movie", season=1, episode=1):
    """Récupère les sous-titres Français et Anglais depuis l'addon public OpenSubtitles v3 Stremio."""
    imdb_id = (imdb_id or "").strip()
    if not imdb_id or not imdb_id.startswith("tt"):
        return []
    if media_type == "series":
        target = f"series/{imdb_id}:{int(season or 1)}:{int(episode or 1)}"
    else:
        target = f"movie/{imdb_id}"
    cache_key = f"opensubs:{target}"

    def _do():
        try:
            url = f"https://opensubtitles-v3.strem.io/subtitles/{target}.json"
            data = http_json(url, timeout=6)
            subs = data.get("subtitles") or []
        except Exception:
            return []

        fr_list = []
        en_list = []
        seen_urls = set()
        for s in subs:
            u = (s.get("url") or "").strip()
            lg = (s.get("lang") or "").lower()
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            if lg in ("fre", "fra", "fr"):
                fr_list.append(u)
            elif lg in ("eng", "en"):
                en_list.append(u)

        result = []
        for idx, u in enumerate(fr_list[:4], 1):
            result.append({
                "id": f"fr_{idx}",
                "lang": "fr",
                "label": f"Français #{idx}",
                "url": u,
                "vtt_url": f"/api/subtitle.vtt?url={urllib.parse.quote(u)}",
            })
        for idx, u in enumerate(en_list[:3], 1):
            result.append({
                "id": f"en_{idx}",
                "lang": "en",
                "label": f"English #{idx}",
                "url": u,
                "vtt_url": f"/api/subtitle.vtt?url={urllib.parse.quote(u)}",
            })
        return result

    return cached_get(cache_key, 1800, _do) or []


def srt_to_vtt(srt_text: str) -> str:
    """Convertit un fichier de sous-titres SRT en format WebVTT standard pour le lecteur HTML5."""
    text = (srt_text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    vtt_body = re.sub(
        r"(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2 --> \3.\4",
        text,
    )
    return "WEBVTT\n\n" + vtt_body


def parse_srt_cues(srt_text: str):
    """Parse un fichier SRT en une liste de cues JSON [{'start': float, 'end': float, 'text': str}]."""
    text = (srt_text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []
    time_re = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = None
        text_start_idx = 1
        for i, ln in enumerate(lines[:2]):
            m = time_re.search(ln)
            if m:
                text_start_idx = i + 1
                break
        if not m or text_start_idx >= len(lines):
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        cue_text = "\n".join(lines[text_start_idx:])
        cue_text = re.sub(r"\{[^}]+\}", "", cue_text)
        cue_text = re.sub(r"<[^>]+>", "", cue_text).strip()
        if cue_text:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": cue_text})
    return cues



def download_top_subtitles_for_mpv(imdb_id, media_type="movie", season=1, episode=1):
    """Télécharge en cache local (/tmp) le meilleur sous-titre complet FR et EN pour injection dans IINA / MPV."""
    subs = fetch_opensubtitles(imdb_id, media_type, season, episode)
    if not subs:
        return []
    picked = []
    for lang_code, fname in (("fr", "/tmp/kino_sub_fr.srt"), ("en", "/tmp/kino_sub_en.srt")):
        cands = [s for s in subs if s["lang"] == lang_code]
        best_raw = b""
        for item in cands[:3]:
            try:
                req = urllib.request.Request(item["url"], headers=HEADERS)
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    raw = resp.read()
                if len(raw) > len(best_raw):
                    best_raw = raw
                if len(raw) >= 6000:
                    best_raw = raw
                    break
            except Exception:
                continue
        if best_raw and len(best_raw) > 64:
            try:
                Path(fname).write_bytes(best_raw)
                picked.append(fname)
            except Exception:
                pass
    return picked


def resolve_trailer_info(title="", year="", yt_id="", lang="vf"):
    """Recherche une bande-annonce VF/VO sur YouTube et tente d'extraire le flux MP4 direct (sans pub ni erreur d'intégration)."""
    title = (title or "").strip()
    year = str(year or "").strip()[:4]
    yt_id = (yt_id or "").strip()
    lang = (lang or "vf").strip().lower()
    cache_key = f"trailer:{title}:{year}:{yt_id}:{lang}"

    def _resolve():
        chosen_id = yt_id if (yt_id and lang == "vo") else ""
        if not chosen_id and title:
            suffix = "bande annonce VF" if lang == "vf" else "official trailer"
            q = urllib.parse.quote(f"{title} {year} {suffix}".strip())
            try:
                req = urllib.request.Request(
                    f"https://www.youtube.com/results?search_query={q}",
                    headers=HEADERS,
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                ids = list(dict.fromkeys(re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)))
                if ids:
                    chosen_id = ids[0]
            except Exception:
                pass
        if not chosen_id:
            chosen_id = yt_id
        if not chosen_id:
            return {"yt_id": "", "stream_url": "", "embed_url": ""}

        stream_url = ""
        ytdlp_bin = shutil.which("yt-dlp") or ("/opt/homebrew/bin/yt-dlp" if os.path.exists("/opt/homebrew/bin/yt-dlp") else None)
        if ytdlp_bin:
            try:
                proc = subprocess.run(
                    [
                        ytdlp_bin,
                        "-g",
                        "--extractor-args",
                        "youtube:player_client=android,web",
                        "-f",
                        "b",
                        "--no-playlist",
                        f"https://www.youtube.com/watch?v={chosen_id}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("http")]
                if lines:
                    stream_url = lines[0]
            except Exception:
                pass

        return {
            "yt_id": chosen_id,
            "stream_url": stream_url,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{chosen_id}?autoplay=1&rel=0",
            "watch_url": f"https://www.youtube.com/watch?v={chosen_id}",
        }

    return cached_get(cache_key, 3600, _resolve)


def get_series_meta(imdb_id):
    return get_media_meta(imdb_id, "series")


def _compute_next_series_episode(imdb_id, season, episode):
    s = int(season or 1)
    e = int(episode or 1)
    if not imdb_id:
        return s, e + 1
    try:
        hit = (
            MEM_CACHE.get(f"meta_fr_v2:series:{imdb_id}")
            or MEM_CACHE.get(f"meta_fr:series:{imdb_id}")
            or MEM_CACHE.get(f"meta:series:{imdb_id}")
        )
        meta = hit["val"] if hit else None
        if not meta:
            return s, e + 1
        videos = meta.get("videos") or []
        same_season_next = [
            int(v.get("episode") or v.get("number") or 0)
            for v in videos
            if int(v.get("season") or 0) == s and int(v.get("episode") or v.get("number") or 0) > e
        ]
        if same_season_next:
            return s, min(same_season_next)
        next_season_eps = [
            int(v.get("episode") or v.get("number") or 0)
            for v in videos
            if int(v.get("season") or 0) == s + 1 and int(v.get("episode") or v.get("number") or 0) > 0
        ]
        if next_season_eps:
            return s + 1, min(next_season_eps)
    except Exception:
        pass
    return s, e + 1


def toggle_watchlist(item):
    return kino_db.db_toggle_watchlist(item)


def _parse_title_year_str(raw_str):
    """Sépare 'A Ghost Story (2017)' en ('A Ghost Story', '2017')."""
    s = html_unescape((raw_str or "").strip())
    if not s:
        return "", ""
    m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


def html_unescape(text):
    import html as _html
    return _html.unescape(text or "")


def fetch_letterboxd_entries_from_url(url_or_user, max_pages=5, target_section="watchlist"):
    """Récupère les films (titre, année) depuis un pseudo ou une URL publique Letterboxd (watchlist, films/watched, ou list)."""
    raw = (url_or_user or "").strip()
    if not raw:
        return []
    sec = "films" if target_section == "watched" else "watchlist"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        username = raw.lstrip("@").strip("/").split("/")[0].strip()
        base_url = f"https://letterboxd.com/{username}/{sec}/"
    else:
        base_url = raw.split("?")[0].rstrip("/") + "/"
        parts = [p for p in urllib.parse.urlparse(base_url).path.split("/") if p]
        if len(parts) == 1 and "letterboxd.com" in base_url:
            base_url = f"https://letterboxd.com/{parts[0]}/{sec}/"
        elif len(parts) == 2 and parts[1] in ("watchlist", "films") and target_section in ("watchlist", "watched"):
            base_url = f"https://letterboxd.com/{parts[0]}/{sec}/"

    entries = []
    seen_keys = set()
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for page in range(1, max_pages + 1):
        page_url = base_url if page == 1 else f"{base_url}page/{page}/"
        try:
            req = urllib.request.Request(page_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=9) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            if page == 1:
                raise RuntimeError(f"Impossible de lire la page Letterboxd ({base_url}) : vérifiez que le profil est public.")
            break

        page_found = 0
        # 1. Attribut moderne Letterboxd : data-item-name="Title (Year)"
        for raw_item in re.findall(r'data-item-name="([^"]+)"', body):
            title, year = _parse_title_year_str(raw_item)
            if title:
                key = f"{title.lower()}|{year}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    entries.append({"title": title, "year": year})
                    page_found += 1

        # 2. Fallback pour anciennes pages / listes : data-film-name + data-film-release-year
        if page_found == 0:
            for m in re.finditer(r'data-film-name="([^"]+)"[^>]*?(?:data-film-release-year="(\d{4})")?', body):
                title = html_unescape(m.group(1).strip())
                year = (m.group(2) or "").strip()
                if title:
                    key = f"{title.lower()}|{year}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        entries.append({"title": title, "year": year})
                        page_found += 1

        # 3. Fallback flux RSS Letterboxd
        if page_found == 0 and "<letterboxd:filmTitle>" in body:
            for m in re.finditer(r"<letterboxd:filmTitle>([^<]+)</letterboxd:filmTitle>\s*(?:<letterboxd:filmYear>(\d{4})</letterboxd:filmYear>)?", body):
                title = html_unescape(m.group(1).strip())
                year = (m.group(2) or "").strip()
                if title:
                    key = f"{title.lower()}|{year}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        entries.append({"title": title, "year": year})
                        page_found += 1

        if page_found == 0 or f"/page/{page + 1}/" not in body:
            break

    return entries


def parse_letterboxd_csv_or_text(csv_text):
    """Extrait une liste de {'title', 'year', 'imdb_id'} depuis un fichier CSV Letterboxd / IMDb ou une liste texte."""
    import csv
    import io
    text = (csv_text or "").lstrip("\ufeff").strip()
    if not text:
        return []

    entries = []
    seen = set()

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    name_idx = next((i for i, h in enumerate(header) if h in ("name", "title", "film", "movie", "original title")), -1)
    year_idx = next((i for i, h in enumerate(header) if h in ("year", "release year", "année")), -1)
    imdb_idx = next((i for i, h in enumerate(header) if h in ("const", "imdb", "imdb_id", "tconst")), -1)

    if name_idx != -1:
        for r in rows[1:]:
            if name_idx >= len(r):
                continue
            raw_title = r[name_idx].strip()
            if not raw_title:
                continue
            title, parsed_yr = _parse_title_year_str(raw_title)
            year = r[year_idx].strip() if (year_idx != -1 and year_idx < len(r)) else parsed_yr
            imdb_id = r[imdb_idx].strip() if (imdb_idx != -1 and imdb_idx < len(r)) else ""
            key = f"{imdb_id or title.lower()}|{year}"
            if key not in seen:
                seen.add(key)
                entries.append({"title": title, "year": year, "imdb_id": imdb_id})
        return entries

    for r in rows:
        line = " ".join(c.strip() for c in r if c.strip())
        if not line or line.lower().startswith("date,name"):
            continue
        title, year = _parse_title_year_str(line)
        if title:
            key = f"{title.lower()}|{year}"
            if key not in seen:
                seen.add(key)
                entries.append({"title": title, "year": year, "imdb_id": ""})
    return entries


def _resolve_letterboxd_entries(raw_entries, max_items=150):
    from concurrent.futures import ThreadPoolExecutor
    batch = (raw_entries or [])[:max_items]
    if not batch:
        return []

    def _resolve_one(entry):
        title = (entry.get("title") or "").strip()
        target_yr = (entry.get("year") or "").strip()
        imdb_id = (entry.get("imdb_id") or "").strip()
        if not title and not imdb_id:
            return None
        try:
            results = search_cinemeta(title or imdb_id, "movie")
            if not results:
                return None
            chosen = None
            t_low = title.lower()
            if imdb_id and imdb_id.startswith("tt"):
                chosen = next((m for m in results if m.get("id") == imdb_id), None)
            if not chosen and target_yr.isdigit():
                ty = int(target_yr)
                # 1. Exact title + year match (±2 years for festival vs international release)
                for m in results:
                    m_name = str(m.get("name") or "").strip().lower()
                    ry_str = str(m.get("releaseInfo") or m.get("year") or "")[:4]
                    if m_name == t_low and ry_str.isdigit() and abs(int(ry_str) - ty) <= 2:
                        chosen = m
                        break
                # 2. Substring/prefix title + year match
                if not chosen:
                    for m in results:
                        m_name = str(m.get("name") or "").strip().lower()
                        ry_str = str(m.get("releaseInfo") or m.get("year") or "")[:4]
                        if (t_low in m_name or m_name in t_low) and ry_str.isdigit() and abs(int(ry_str) - ty) <= 1:
                            chosen = m
                            break
                # 3. Year match
                if not chosen:
                    for m in results:
                        ry_str = str(m.get("releaseInfo") or m.get("year") or "")[:4]
                        if ry_str.isdigit() and abs(int(ry_str) - ty) <= 1:
                            chosen = m
                            break
            if not chosen:
                chosen = next((m for m in results if str(m.get("name") or "").strip().lower() == t_low), results[0])
            if not chosen or not chosen.get("id"):
                return None
            cid = chosen.get("id")
            rating_str = str(chosen.get("imdbRating") or "").strip()
            if not rating_str and cid.startswith("tt"):
                try:
                    mdata = cached_get(
                        f"cinemeta_lite:{cid}",
                        86400,
                        lambda: http_json(f"https://v3-cinemeta.strem.io/meta/movie/{cid}.json", timeout=4),
                    )
                    m_inner = (mdata or {}).get("meta") or {}
                    rating_str = str(m_inner.get("imdbRating") or "").strip()
                except Exception:
                    pass
            return {
                "id": cid,
                "name": chosen.get("name") or title,
                "type": "movie",
                "year": str(chosen.get("releaseInfo") or chosen.get("year") or target_yr)[:4],
                "poster": chosen.get("poster") or f"https://images.metahub.space/poster/medium/{cid}/img",
                "imdbRating": rating_str,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        return [r for r in pool.map(_resolve_one, batch) if r]


def import_letterboxd_watchlist(payload):
    """Importe une Watchlist et/ou les Films déjà vus depuis Letterboxd (via URL/pseudo ou CSV/texte)."""
    url_or_user = (payload.get("url_or_user") or "").strip()
    csv_text = (payload.get("csv_text") or "").strip()
    csv_filename = (payload.get("csv_filename") or "").lower()
    mode = (payload.get("mode") or "both").strip().lower()
    if mode not in ("watchlist", "watched", "both"):
        mode = "both"

    # Si l'utilisateur a collé une URL explicite finissant par /films/, forcer watched
    if "/films" in url_or_user.lower() and mode == "watchlist":
        mode = "watched"

    wl_raw = []
    watched_raw = []

    if url_or_user:
        if mode in ("watchlist", "both"):
            try:
                wl_raw.extend(fetch_letterboxd_entries_from_url(url_or_user, max_pages=4, target_section="watchlist"))
            except Exception:
                if mode == "watchlist":
                    raise
        if mode in ("watched", "both"):
            try:
                watched_raw.extend(fetch_letterboxd_entries_from_url(url_or_user, max_pages=5, target_section="watched"))
            except Exception:
                if mode == "watched":
                    raise

    if csv_text:
        parsed_csv = parse_letterboxd_csv_or_text(csv_text)
        is_watched = mode == "watched" or any(k in csv_filename for k in ("watched", "rating", "diary", "history"))
        if is_watched:
            watched_raw.extend(parsed_csv)
        else:
            wl_raw.extend(parsed_csv)

    if not wl_raw and not watched_raw:
        raise RuntimeError("Aucun film trouvé. Vérifiez le pseudo/lien Letterboxd ou le fichier CSV.")

    resolved_wl = _resolve_letterboxd_entries(wl_raw, max_items=120) if wl_raw else []
    resolved_watched = _resolve_letterboxd_entries(watched_raw, max_items=180) if watched_raw else []

    cfg = load_config()
    wl = list(cfg.get("watchlist", []))
    hist = list(cfg.get("history", []))

    existing_wl_map = {x.get("id"): x for x in wl if x.get("id")}
    added_wl_count = 0
    for item in reversed(resolved_wl):
        iid = item["id"]
        if iid not in existing_wl_map:
            existing_wl_map[iid] = item
            wl.insert(0, item)
            added_wl_count += 1
        elif item.get("imdbRating") and not existing_wl_map[iid].get("imdbRating"):
            existing_wl_map[iid]["imdbRating"] = item["imdbRating"]

    existing_hist_map = {x.get("id"): x for x in hist if x.get("id")}
    added_watched_count = 0
    now_ts = int(time.time())
    for item in resolved_watched:
        iid = item["id"]
        prev = existing_hist_map.get(iid)
        if prev and (prev.get("completed") or float(prev.get("progress_pct") or 0) >= 85.0):
            if item.get("imdbRating") and not prev.get("imdbRating"):
                prev["imdbRating"] = item.get("imdbRating", "")
            continue
        added_watched_count += 1
        hist = [x for x in hist if x.get("id") != iid]
        hist.append({
            "id": iid,
            "name": item.get("name", ""),
            "type": "movie",
            "year": item.get("year", ""),
            "poster": item.get("poster", ""),
            "imdbRating": item.get("imdbRating", ""),
            "position": 7200,
            "duration": 7200,
            "progress_pct": 100.0,
            "completed": True,
            "imported_watched": True,
            "updated_at": now_ts,
        })
        existing_hist_map[iid] = hist[-1]

    wl = wl[:500]
    hist = hist[:1000]
    cfg_updates = {"watchlist": wl, "history": hist}
    if url_or_user and not url_or_user.startswith("http") and "/" not in url_or_user:
        cfg_updates["letterboxd_user"] = url_or_user.strip().lstrip("@")
    save_config(cfg_updates)
    return {
        "watchlist": wl,
        "history": hist,
        "found_count": len(wl_raw) + len(watched_raw),
        "added_count": added_wl_count,
        "found_wl_count": len(wl_raw),
        "added_wl_count": added_wl_count,
        "found_watched_count": len(watched_raw),
        "added_watched_count": added_watched_count,
    }


def import_letterboxd_custom_list(list_url):
    """
    Importe n'importe quelle liste publique Letterboxd par URL :
    Exemples :
    - https://letterboxd.com/official/list/letterboxds-top-500-films/
    - https://letterboxd.com/username/list/my-custom-list/
    - https://boxd.it/...
    """
    raw_url = (list_url or "").strip()
    if not raw_url:
        raise ValueError("URL Letterboxd requise.")

    if "boxd.it" in raw_url:
        try:
            req = urllib.request.Request(raw_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                raw_url = r.geturl()
        except Exception:
            pass

    clean_url = raw_url.split("?")[0].rstrip("/") + "/"
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    }

    list_title = ""
    list_description = ""
    extracted_movies = []
    seen_keys = set()

    for page in range(1, 4):
        page_url = clean_url if page == 1 else f"{clean_url}page/{page}/"
        try:
            req = urllib.request.Request(page_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if page == 1:
                raise RuntimeError(f"Impossible de lire la page Letterboxd ({clean_url}) : {e}")
            break

        if page == 1:
            m_title = re.search(r'<meta property="og:title" content="([^"]+)"', body) or re.search(r'<h1[^>]*>([^<]+)</h1>', body)
            if m_title:
                list_title = html_unescape(m_title.group(1)).replace("&#039;", "'").replace("&amp;", "&").strip()
            m_desc = re.search(r'<meta property="og:description" content="([^"]+)"', body)
            if m_desc:
                list_description = html_unescape(m_desc.group(1)).replace("&#039;", "'").replace("&amp;", "&").strip()

        page_found = 0
        for raw_item in re.findall(r'data-item-name="([^"]+)"', body):
            title, year = _parse_title_year_str(raw_item)
            if title:
                k = f"{title.lower()}|{year}"
                if k not in seen_keys:
                    seen_keys.add(k)
                    extracted_movies.append({"title": title, "year": year})
                    page_found += 1

        if page_found == 0:
            for m in re.finditer(r'data-film-name="([^"]+)"[^>]*?(?:data-film-release-year="(\d{4})")?', body):
                title = html_unescape(m.group(1).strip())
                year = (m.group(2) or "").strip()
                if title:
                    k = f"{title.lower()}|{year}"
                    if k not in seen_keys:
                        seen_keys.add(k)
                        extracted_movies.append({"title": title, "year": year})
                        page_found += 1

        if page_found == 0 or f"/page/{page + 1}/" not in body:
            break

    if not extracted_movies:
        raise RuntimeError(f"Aucun film n'a pu être extrait de cette liste Letterboxd ({clean_url}).")

    if not list_title:
        parts = [p for p in urllib.parse.urlparse(clean_url).path.split("/") if p]
        list_title = (parts[-1] if parts else "Liste Letterboxd").replace("-", " ").title()

    resolved_items = _resolve_letterboxd_entries(extracted_movies, max_items=120)

    list_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", urllib.parse.urlparse(clean_url).path.strip("/"))
    if not list_id:
        list_id = f"list_{int(time.time())}"

    list_entry = {
        "id": list_id,
        "title": list_title,
        "description": list_description,
        "url": clean_url,
        "items": resolved_items,
        "count": len(resolved_items),
        "updated_at": int(time.time()),
    }

    cfg = load_config()
    custom_lists = list(cfg.get("custom_lists", []))
    custom_lists = [l for l in custom_lists if l.get("id") != list_id and l.get("url") != clean_url]
    custom_lists.insert(0, list_entry)
    custom_lists = custom_lists[:20]
    save_config({"custom_lists": custom_lists})

    return {
        "status": "ok",
        "list": list_entry,
        "custom_lists": custom_lists,
        "total_extracted": len(extracted_movies),
        "total_resolved": len(resolved_items),
    }


def delete_letterboxd_custom_list(list_id):
    cfg = load_config()
    custom_lists = [l for l in cfg.get("custom_lists", []) if l.get("id") != list_id]
    save_config({"custom_lists": custom_lists})
    return custom_lists


def record_history(entry):
    s_num = entry.get("season")
    e_num = entry.get("episode")
    item_id = entry.get("id") or entry.get("filename")
    if s_num and e_num and item_id:
        try:
            ns, ne = _compute_next_series_episode(item_id, s_num, e_num)
            entry["next_season"] = ns
            entry["next_episode"] = ne
        except Exception:
            pass
    return kino_db.db_record_history(entry)


def toggle_watched_status(payload):
    """Marque ou démarque un film, un épisode ou une saison entière de série comme 'Vu' (✓ Vu)."""
    cfg = load_config()
    hist = cfg.get("history", [])
    item_id = (payload.get("id") or "").strip()
    if not item_id:
        return hist
    mtype = payload.get("type", "movie")
    force_watched = bool(payload.get("force_watched"))
    existing = next((x for x in hist if x.get("id") == item_id), None)

    if mtype == "series" and payload.get("season") and payload.get("season_all"):
        s = int(payload["season"])
        ep_nums = [int(x) for x in (payload.get("episodes") or []) if int(x or 0) > 0]
        if not ep_nums:
            ep_nums = list(range(1, 11))
        season_codes = [f"S{s:02d}E{e:02d}" for e in sorted(set(ep_nums))]
        if existing:
            hist = [x for x in hist if x.get("id") != item_id]
            merged = dict(existing)
        else:
            merged = {
                "id": item_id,
                "name": payload.get("name", ""),
                "type": "series",
                "year": payload.get("year", ""),
                "poster": payload.get("poster", ""),
            }
        watched_eps = list(merged.get("watched_episodes") or [])
        ep_positions = dict(merged.get("ep_positions") or {})
        all_already_watched = all(c in watched_eps for c in season_codes)
        if all_already_watched and not force_watched:
            watched_eps = [c for c in watched_eps if c not in season_codes]
            for c in season_codes:
                ep_positions.pop(c, None)
            if merged.get("season") == s:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
        else:
            for c in season_codes:
                if c not in watched_eps:
                    watched_eps.append(c)
                ep_positions[c] = {"pos": 2700, "dur": 2700, "pct": 100.0}
            last_ep = max(ep_nums)
            merged["season"] = s
            merged["episode"] = last_ep
            merged["completed"] = True
            merged["progress_pct"] = 100.0
            merged["position"] = 2700
            merged["duration"] = 2700
            ns, ne = _compute_next_series_episode(item_id, s, last_ep)
            merged["next_season"] = ns
            merged["next_episode"] = ne
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        save_config({"history": hist})
        return hist

    if mtype == "series" and payload.get("season") and payload.get("episode"):
        s = int(payload["season"])
        e = int(payload["episode"])
        ep_code = f"S{s:02d}E{e:02d}"
        if existing:
            hist = [x for x in hist if x.get("id") != item_id]
            merged = dict(existing)
        else:
            merged = {
                "id": item_id,
                "name": payload.get("name", ""),
                "type": "series",
                "year": payload.get("year", ""),
                "poster": payload.get("poster", ""),
            }
        watched_eps = list(merged.get("watched_episodes") or [])
        ep_positions = dict(merged.get("ep_positions") or {})
        if ep_code in watched_eps and not force_watched:
            watched_eps = [c for c in watched_eps if c != ep_code]
            ep_positions.pop(ep_code, None)
            if merged.get("season") == s and merged.get("episode") == e:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
        else:
            if ep_code not in watched_eps:
                watched_eps.append(ep_code)
            ep_positions[ep_code] = {"pos": 2700, "dur": 2700, "pct": 100.0}
            merged["season"] = s
            merged["episode"] = e
            merged["completed"] = True
            merged["progress_pct"] = 100.0
            merged["position"] = 2700
            merged["duration"] = 2700
            ns, ne = _compute_next_series_episode(item_id, s, e)
            merged["next_season"] = ns
            merged["next_episode"] = ne
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        save_config({"history": hist})
        return hist

    # Film ou Série entière (depuis la roulette ou une carte)
    is_currently_done = bool(existing and (existing.get("completed") or float(existing.get("progress_pct") or 0) >= 85.0))
    if is_currently_done and not force_watched:
        hist = [x for x in hist if x.get("id") != item_id]
        save_config({"history": hist})
        return hist
    return record_history({
        "id": item_id,
        "name": payload.get("name", ""),
        "type": mtype,
        "year": payload.get("year", ""),
        "poster": payload.get("poster", ""),
        "imdbRating": payload.get("imdbRating", ""),
        "position": 7200,
        "duration": 7200,
    })


def _bdecode_slice(data: bytes, idx: int = 0):
    """Décodeur Bencode léger qui conserve la tranche d'octets exacte du dictionnaire 'info'."""
    ch = data[idx : idx + 1]
    if ch == b"i":
        end_i = data.index(b"e", idx + 1)
        return int(data[idx + 1 : end_i]), end_i + 1
    if ch == b"l":
        idx += 1
        arr = []
        while data[idx : idx + 1] != b"e":
            v, idx = _bdecode_slice(data, idx)
            arr.append(v)
        return arr, idx + 1
    if ch == b"d":
        idx += 1
        dct = {}
        while data[idx : idx + 1] != b"e":
            k, idx = _bdecode_slice(data, idx)
            val_start = idx
            v, idx = _bdecode_slice(data, idx)
            val_end = idx
            k_str = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k)
            dct[k_str] = v
            if k_str == "info" and isinstance(v, dict):
                dct["__info_raw__"] = data[val_start:val_end]
        return dct, idx + 1
    if ch and 48 <= ch[0] <= 57:
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        s_start = colon + 1
        s_end = s_start + length
        return data[s_start:s_end], s_end
    raise ValueError("Format Bencode (.torrent) invalide")


def torrent_file_to_magnet(raw_bytes: bytes):
    """Convertit le contenu binaire d'un fichier .torrent en lien magnet:?xt=urn:btih:..."""
    meta, _ = _bdecode_slice(raw_bytes, 0)
    if not isinstance(meta, dict) or "__info_raw__" not in meta:
        raise RuntimeError("Ce fichier .torrent ne contient pas de section 'info' valide.")
    info_raw = meta["__info_raw__"]
    info_hash = hashlib.sha1(info_raw).hexdigest().lower()
    info_dict = meta.get("info") or {}
    raw_name = info_dict.get("name.utf-8") or info_dict.get("name") or b"Torrent"
    name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
    trackers = []
    ann = meta.get("announce")
    if isinstance(ann, bytes):
        trackers.append(ann.decode("utf-8", errors="ignore"))
    ann_list = meta.get("announce-list")
    if isinstance(ann_list, list):
        for tier in ann_list:
            if isinstance(tier, list):
                for tr in tier:
                    if isinstance(tr, bytes):
                        u = tr.decode("utf-8", errors="ignore")
                        if u and u not in trackers:
                            trackers.append(u)
    tr_qs = "".join(f"&tr={urllib.parse.quote(tr)}" for tr in trackers[:8])
    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}{tr_qs}"
    return {"magnet": magnet, "name": name, "info_hash": info_hash}


def get_resume_position(imdb_id, season=None, episode=None):
    if not imdb_id:
        return 0
    cfg = load_config()
    hist = cfg.get("history", [])
    item = next((x for x in hist if x.get("id") == imdb_id), None)
    if not item:
        return 0
    if season and episode:
        ep_code = f"S{int(season):02d}E{int(episode):02d}"
        ep_pos = (item.get("ep_positions") or {}).get(ep_code)
        if ep_pos:
            pct = ep_pos.get("pct", 0)
            if 1.0 <= pct < 85.0:
                return int(ep_pos.get("pos", 0))
            return 0
    pct = item.get("progress_pct", 0)
    if 1.0 <= pct < 85.0:
        if not season or (item.get("season") == int(season) and item.get("episode") == int(episode)):
            return int(item.get("position", 0))
    return 0


def remove_history(item_id):
    if item_id == "__all__":
        with kino_db._DB_LOCK:
            conn = kino_db.get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM watch_history;")
            finally:
                conn.close()
        return []
    return kino_db.db_remove_history(item_id)


def _parse_rd_iso_age_days(iso_str):
    if not iso_str:
        return 0.0
    try:
        if isinstance(iso_str, (int, float)):
            return max(0.0, (time.time() - float(iso_str)) / 86400.0)
        from datetime import datetime, timezone
        clean = str(iso_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _get_provider_and_token(token=None, provider=None):
    cfg = load_config()
    prov = (provider or cfg.get("debrid_provider") or "realdebrid").strip().lower()
    if prov not in DEBRID_PROVIDERS:
        prov = "realdebrid"
    tok = (token if token is not None else cfg.get("rd_token") or "").strip()
    return prov, tok


def rd_get_downloads(token=None, limit=50, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return []

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        res = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}")
        magnets = (res.get("data") or {}).get("magnets") or []
        if isinstance(magnets, dict):
            magnets = list(magnets.values())
        items = []
        from datetime import datetime, timezone
        for m in magnets[:int(limit)]:
            if m.get("statusCode") != 4:
                continue
            mid = str(m.get("id", ""))
            up_ts = m.get("uploadDate") or 0
            gen_date = datetime.fromtimestamp(up_ts, tz=timezone.utc).strftime("%Y-%m-%d") if up_ts else ""
            links = m.get("links") or []
            if links:
                first_l = links[0]
                fname = first_l.get("filename") or m.get("filename") or "Fichier AllDebrid"
                fsize = first_l.get("size") or m.get("size") or 0
                raw_link = first_l.get("link", "")
                dl_api = f"http://127.0.0.1:{PORT}/api/ad-dl?link={urllib.parse.quote(raw_link)}" if raw_link else ""
                items.append({
                    "id": mid,
                    "ids": [mid],
                    "filename": fname,
                    "filesize": format_size(fsize),
                    "download": dl_api,
                    "generated": gen_date,
                    "age_days": round(_parse_rd_iso_age_days(up_ts), 1),
                })
        return items

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json(f"https://debrid-link.com/api/v2/seedbox/list?perPage={int(limit)}", headers=auth)
        torrents = res.get("value") or []
        items = []
        from datetime import datetime, timezone
        for t in torrents:
            tid = str(t.get("id", ""))
            created = t.get("created") or 0
            gen_date = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d") if created else ""
            files = t.get("files") or []
            vids = [f for f in files if f.get("downloadUrl")]
            target = vids[0] if vids else (files[0] if files else None)
            if not target or not target.get("downloadUrl"):
                continue
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": target.get("name") or t.get("name") or "Fichier Debrid-Link",
                "filesize": format_size(target.get("size") or t.get("totalSize") or 0),
                "download": target["downloadUrl"],
                "generated": gen_date,
                "age_days": round(_parse_rd_iso_age_days(created), 1),
            })
        return items

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://api.torbox.app/v1/api/torrents/mylist?bypass_cache=true", headers=auth)
        torrents = res.get("data") or []
        items = []
        for t in torrents[:int(limit)]:
            if not t.get("download_present"):
                continue
            tid = str(t.get("id", ""))
            files = t.get("files") or []
            if not files:
                continue
            largest = max(files, key=lambda x: x.get("size", 0))
            fid = largest.get("id", 0)
            dl_api = f"http://127.0.0.1:{PORT}/api/torbox-dl?torrent_id={tid}&file_id={fid}"
            created_str = (t.get("created_at") or "")[:10]
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": largest.get("short_name") or largest.get("name") or t.get("name") or "Fichier TorBox",
                "filesize": format_size(largest.get("size") or t.get("size") or 0),
                "download": dl_api,
                "generated": created_str,
                "age_days": round(_parse_rd_iso_age_days(t.get("created_at", "")), 1),
            })
        return items

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/transfer/list?{q}")
        transfers = res.get("transfers") or []
        items = []
        for t in transfers[:int(limit)]:
            tid = str(t.get("id", ""))
            fname = t.get("name") or "Fichier Premiumize"
            dl = t.get("link") or ""
            if not dl:
                continue
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": fname,
                "filesize": "Cloud PM",
                "download": dl,
                "generated": "",
                "age_days": 0.0,
            })
        return items

    if prov != "realdebrid":
        return []

    auth = {"Authorization": f"Bearer {token}"}
    data = http_json(f"https://api.real-debrid.com/rest/1.0/downloads?limit={int(limit)}", headers=auth)
    if not isinstance(data, list):
        return []
    by_name = {}
    items = []
    for d in data:
        fname = d.get("filename", "")
        dl = d.get("download", "")
        did = d.get("id", "")
        if not fname or not dl or not did:
            continue
        if fname in by_name:
            by_name[fname]["ids"].append(did)
            continue
        entry = {
            "id": did,
            "ids": [did],
            "filename": fname,
            "filesize": format_size(d.get("filesize", 0)),
            "download": dl,
            "generated": (d.get("generated") or "")[:10],
            "age_days": round(_parse_rd_iso_age_days(d.get("generated", "")), 1),
        }
        by_name[fname] = entry
        items.append(entry)
    return items


def rd_delete_downloads(token=None, ids=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token or not ids:
        return 0
    if isinstance(ids, str):
        ids = [ids]
    deleted = 0

    if prov == "alldebrid":
        for mid in ids:
            try:
                q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "id": mid})
                http_json(f"https://api.alldebrid.com/v4/magnet/delete?{q}")
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        for tid in ids:
            try:
                http_json(f"https://debrid-link.com/api/v2/seedbox/{tid}/remove", method="DELETE", headers=auth)
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        for tid in ids:
            try:
                http_json(
                    "https://api.torbox.app/v1/api/torrents/controltorrent",
                    method="POST",
                    json_data={"torrent_id": int(tid) if str(tid).isdigit() else tid, "operation": "delete"},
                    headers=auth,
                )
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "premiumize":
        for tid in ids:
            try:
                q = urllib.parse.urlencode({"apikey": token})
                http_json(f"https://www.premiumize.me/api/transfer/delete?{q}", method="POST", data={"id": tid})
                deleted += 1
            except Exception:
                pass
        return deleted

    auth = {"Authorization": f"Bearer {token}"}
    for did in ids:
        if not did:
            continue
        try:
            http_json(f"https://api.real-debrid.com/rest/1.0/downloads/delete/{did}", method="DELETE", headers=auth)
            deleted += 1
        except Exception:
            pass
    return deleted


def rd_cleanup_cloud(token=None, max_age_days=None, provider=None):
    """Supprime du Cloud du débrideur actif (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize) les éléments plus anciens que max_age_days (ou tout si max_age_days == -1)."""
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return {"deleted_downloads": 0, "deleted_torrents": 0}
    if max_age_days is None:
        max_age_days = int(load_config().get("rd_retention_days", 0) or 0)
    if max_age_days == 0:
        return {"deleted_downloads": 0, "deleted_torrents": 0}

    if prov in ("alldebrid", "debridlink", "torbox", "premiumize"):
        items = rd_get_downloads(token, limit=100, provider=prov)
        to_del = [
            it["id"] for it in items
            if max_age_days == -1 or float(it.get("age_days", 0)) >= float(max_age_days)
        ]
        cnt = rd_delete_downloads(token, to_del, provider=prov) if to_del else 0
        return {"deleted_downloads": cnt, "deleted_torrents": 0}

    if prov != "realdebrid":
        return {"deleted_downloads": 0, "deleted_torrents": 0}

    auth = {"Authorization": f"Bearer {token}"}
    del_dl = 0
    del_tor = 0

    try:
        downloads = http_json("https://api.real-debrid.com/rest/1.0/downloads?limit=100", headers=auth)
        if isinstance(downloads, list):
            for d in downloads:
                did = d.get("id")
                if not did:
                    continue
                age = _parse_rd_iso_age_days(d.get("generated", ""))
                if max_age_days == -1 or age >= float(max_age_days):
                    try:
                        http_json(f"https://api.real-debrid.com/rest/1.0/downloads/delete/{did}", method="DELETE", headers=auth)
                        del_dl += 1
                    except Exception:
                        pass
    except Exception:
        pass

    try:
        torrents = http_json("https://api.real-debrid.com/rest/1.0/torrents?limit=100", headers=auth)
        if isinstance(torrents, list):
            for t in torrents:
                tid = t.get("id")
                if not tid:
                    continue
                age = _parse_rd_iso_age_days(t.get("added", ""))
                if max_age_days == -1 or age >= float(max_age_days):
                    try:
                        http_json(f"https://api.real-debrid.com/rest/1.0/torrents/delete/{tid}", method="DELETE", headers=auth)
                        del_tor += 1
                    except Exception:
                        pass
    except Exception:
        pass

    return {"deleted_downloads": del_dl, "deleted_torrents": del_tor}


def parse_torrent_tags(text):
    p = torrent_engine.parse_release_details(text)
    return p.get("display_badges", []), p.get("langs", [])


def clean_meta_text(text):
    """Nettoie les métadonnées pour un affichage sobre, précis et sans emojis superflus."""
    return torrent_engine.clean_meta_text(text)


def _parse_stremio_streams(streams, default_source="Torrentio"):
    parsed = []
    for s in streams or []:
        raw_title = s.get("title") or s.get("description") or ""
        source_tag = (s.get("name") or default_source).replace("\n", " ").strip()
        lines = [line.strip() for line in raw_title.split("\n") if line.strip()]
        release_name = lines[0] if lines else "Stream"
        meta_info = clean_meta_text(" • ".join(lines[1:])) if len(lines) > 1 else ""

        # Extraction structurée des seeders et de la taille
        seeders = int(s.get("seed") or s.get("seeders") or 0)
        if not seeders:
            m_seed = re.search(r"👤\s*(\d+)", raw_title) or re.search(r"\b(\d+)\s*(?:seeders?|seeds?|pairs?)\b", raw_title, re.IGNORECASE)
            seeders = int(m_seed.group(1)) if m_seed else 0

        size_bytes = int(s.get("sizebytes") or s.get("size") or 0)
        size_gb = 0.0
        size_str = ""
        if size_bytes > 0:
            size_gb = round(size_bytes / (1024.0 * 1024.0 * 1024.0), 2)
            size_str = f"{size_gb} GB"
        else:
            m_gb = re.search(r"💾\s*([\d\.]+)\s*GB", raw_title, re.IGNORECASE) or re.search(r"([\d\.]+)\s*(?:GB|GiB)", raw_title, re.IGNORECASE)
            m_mb = re.search(r"💾\s*([\d\.]+)\s*MB", raw_title, re.IGNORECASE) or re.search(r"([\d\.]+)\s*(?:MB|MiB)", raw_title, re.IGNORECASE)
            if m_gb:
                size_gb = round(float(m_gb.group(1)), 2)
                size_str = f"{size_gb} GB"
            elif m_mb:
                size_gb = round(float(m_mb.group(1)) / 1024.0, 3)
                size_str = f"{m_mb.group(1)} MB"

        resolve_url = s.get("url", "")
        info_hash = (s.get("infoHash") or "").lower()
        if not info_hash and resolve_url:
            m = re.search(r"/([a-fA-F0-9]{40})/", resolve_url)
            if m:
                info_hash = m.group(1).lower()

        if not info_hash and not resolve_url:
            continue

        st_lower = source_tag.lower()
        is_instant = any(f"[{b}]" in st_lower for b in ("rd+", "ad+", "tb+", "dl+", "pm+", "oc+", "ed+"))
        qualities, langs = parse_torrent_tags(f"{source_tag} {raw_title}")
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(release_name)}" if info_hash else resolve_url

        display_meta = meta_info
        if display_meta.startswith(f"{default_source} • "):
            display_meta = display_meta[len(f"{default_source} • "):]
        elif display_meta == default_source:
            display_meta = ""

        parsed.append({
            "source": default_source,
            "title": release_name,
            "meta": display_meta,
            "qualities": qualities,
            "langs": langs,
            "magnet": magnet,
            "resolve_url": resolve_url,
            "is_instant": is_instant,
            "info_hash": info_hash,
            "file_idx": s.get("fileIdx"),
            "seeders": seeders,
            "size_gb": size_gb,
            "size_str": size_str,
        })
    return parsed


def is_plausible_torrent_size(t, media_type="movie", runtime_minutes=None):
    """Vérifie via torrent_engine la plausibilité physique et l'absence de CAM/fakes."""
    return torrent_engine.is_plausible_torrent_size(t, media_type=media_type, runtime_minutes=runtime_minutes)


def search_torrentio(imdb_id, media_type="movie", season=1, episode=1, rd_token=None, provider=None, runtime_minutes=None, sort_by="score", query_title=""):
    prov, token = _get_provider_and_token(rd_token, provider)
    prov_meta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])
    tio_key = prov_meta.get("torrentio_key", "")

    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    pref_quality = cfg.get("pref_quality", "4k")
    hdr_mode = cfg.get("hdr_mode", "sdr_pref")

    if runtime_minutes is None and imdb_id and media_type == "movie":
        try:
            m_info = get_media_meta(imdb_id, "movie")
            rt_str = str((m_info or {}).get("runtime") or "")
            m_rt = re.search(r"(\d+)", rt_str)
            if m_rt:
                runtime_minutes = int(m_rt.group(1))
        except Exception:
            pass

    clean_q = query_title.strip() if query_title else ""
    cache_key = f"multi_engine:{prov}:{media_type}:{imdb_id}:{season}:{episode}:{bool(token)}:{sort_by}:{clean_q}"

    def _fetch():
        return torrent_engine.search_multi_torrents(
            imdb_id=imdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            token=token,
            tio_key=tio_key,
            provider=prov,
            runtime_minutes=runtime_minutes,
            sort_by=sort_by,
            pref_lang=pref_lang,
            pref_quality=pref_quality,
            hdr_mode=hdr_mode,
            http_json_fn=http_json,
            search_apibay_fn=search_apibay,
            query_title=clean_q,
        )

    res = list(cached_get(cache_key, 180, _fetch) or [])
    return res


def search_apibay(query):
    encoded = urllib.parse.quote(query)
    url = f"https://apibay.org/q.php?q={encoded}&cat=200"
    data = http_json(url)
    results = []
    if not isinstance(data, list):
        return results
    for item in data:
        info_hash = item.get("info_hash")
        name = item.get("name")
        if not info_hash or info_hash == "0000000000000000000000000000000000000000":
            continue
        seeders = int(item.get("seeders", 0) or 0)
        size_bytes = int(item.get("size", 0) or 0)
        size_gb = round(size_bytes / (1024.0 * 1024.0 * 1024.0), 2)
        size_str = format_size(size_bytes)
        details = torrent_engine.parse_release_details(name)
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}"
        t_entry = {
            "source": "APIBay",
            "title": name,
            "meta": f"{seeders} seeders • {size_str}",
            "qualities": details["display_badges"],
            "langs": details["langs"],
            "magnet": magnet,
            "resolve_url": "",
            "is_instant": False,
            "info_hash": info_hash,
            "file_idx": None,
            "seeders": seeders,
            "size_gb": size_gb,
            "size_str": size_str,
            "parsed_details": details,
        }
        if torrent_engine.is_plausible_torrent_size(t_entry, media_type="movie"):
            results.append(t_entry)
    return results


def score_torrent_for_one_click(t):
    """Calcule un score de priorité pour le mode 'Lecture 1-Clic' selon les préférences utilisateur (Langue & Qualité)."""
    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    pref_quality = cfg.get("pref_quality", "4k")
    hdr_mode = cfg.get("hdr_mode", "sdr_pref")
    return torrent_engine.score_torrent(t, pref_lang=pref_lang, pref_quality=pref_quality, hdr_mode=hdr_mode)


# ==========================================
# MULTI-DEBRID API (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize, Mega-Debrid)
# ==========================================
def rd_get_user(token=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return None
    pmeta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        res = http_json(f"https://api.alldebrid.com/v4/user?{q}")
        if res.get("status") != "success":
            raise RuntimeError((res.get("error") or {}).get("message", "Clé AllDebrid invalide"))
        u = (res.get("data") or {}).get("user") or {}
        rem = max(0, int((u.get("premiumUntil") or 0) - time.time())) if u.get("isPremium") else 0
        return {"username": u.get("username", "AllDebrid"), "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://debrid-link.com/api/v2/account/infos", headers=auth)
        u = res.get("value") or {}
        return {
            "username": u.get("username") or u.get("email") or "Debrid-Link",
            "premium": int(u.get("premiumLeft") or 0),
            "provider": pmeta["name"],
            "short": pmeta["short"],
        }

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://api.torbox.app/v1/api/user/me", headers=auth)
        u = res.get("data") or {}
        exp_str = u.get("premium_expires_at") or ""
        rem = 0
        if exp_str:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                rem = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                rem = 86400 * 30 if u.get("plan", 0) > 0 else 0
        elif u.get("plan", 0) > 0:
            rem = 86400 * 30
        uname = (u.get("email") or "TorBox").split("@")[0]
        return {"username": uname, "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/account/info?{q}")
        if res.get("status") != "success":
            raise RuntimeError(res.get("message", "Clé Premiumize invalide"))
        rem = max(0, int((res.get("premium_until") or 0) - time.time()))
        return {
            "username": str(res.get("customer_id") or "Premiumize"),
            "premium": rem,
            "provider": pmeta["name"],
            "short": pmeta["short"],
        }

    if prov == "megadebrid":
        if ":" in token:
            login, pw = token.split(":", 1)
            q = urllib.parse.urlencode({"form": "connexion", "login": login, "password": pw})
            res = http_json(f"https://www.mega-debrid.eu/index.php?{q}")
            if res.get("response_code") != "ok":
                raise RuntimeError(res.get("response_text", "Identifiants Mega-Debrid invalides"))
            rem = max(0, int(res.get("vip_end", 0)) - int(time.time())) if res.get("vip_end") else 86400
            return {"username": login, "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}
        return {"username": "Mega-Debrid", "premium": 86400 * 30, "provider": pmeta["name"], "short": pmeta["short"]}

    auth = {"Authorization": f"Bearer {token}"}
    u = http_json("https://api.real-debrid.com/rest/1.0/user", headers=auth)
    u["provider"] = pmeta["name"]
    u["short"] = pmeta["short"]
    return u


def resolve_torrentio_rd_url(resolve_url):
    """Résout un lien direct Torrentio ([RD+], [AD+], [TB+], [DL+], [PM+]) vers le flux vidéo final du débrideur."""
    req_headers = dict(HEADERS)
    req_headers["Range"] = "bytes=0-0"
    req = urllib.request.Request(resolve_url, headers=req_headers, method="GET")
    with urllib.request.urlopen(req, timeout=25) as resp:
        final_url = resp.geturl()
        content_range = resp.headers.get("Content-Range", "")
        total_bytes = 0
        if "/" in content_range:
            try:
                total_bytes = int(content_range.split("/")[-1])
            except Exception:
                total_bytes = 0
        if not total_bytes:
            total_bytes = int(resp.headers.get("Content-Length", 0))

    bad_Placeholders = ("downloading.mp4", "cached.mp4", "error.mp4", "non_debrid.mp4")
    if "torrentio.strem.fun" in final_url or any(final_url.lower().endswith(p) for p in bad_Placeholders):
        raise RuntimeError("Flux non disponible en cache instantané sur votre débrideur.")

    prov, _ = _get_provider_and_token()
    badge = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])["badge"]
    filename = urllib.parse.unquote(final_url.split("/")[-1].split("?")[0])
    return {
        "ready": True,
        "torrent_id": "instant_cache",
        "status": "downloaded",
        "progress": 100,
        "files": [{
            "filename": filename,
            "filesize": format_size(total_bytes) if total_bytes > 1024 else f"Prêt (Flux Direct {badge})",
            "filesize_bytes": total_bytes,
            "download": final_url,
            "is_target_ep": True,
        }],
    }


def is_target_episode_file(filename: str, season: int = None, episode: int = None, absolute_ep: int = None) -> bool:
    """
    Détecte avec une haute précision si un nom de fichier correspond exactement à l'épisode recherché.
    Gère les conventions occidentales et les conventions Anime japonaises :
    - S01E05, 1x05, S1E5, S01 - E05
    - Formats animés saisonniers : S2 - 05, Season 2 - 05, 2nd Season - 05, Part 2 - 05
    - Formats animés absolus : ' - 05 ', '[05]', '(05)', 'Ep 05', 'Episode 5', '#05'
    - Numérotation absolue multi-saisons (ex: JJK 47, Bleach 366, One Piece 1089)
    - Rejet strict des fakes, résolutions (1080p), bonus (OP, ED, PV, Sample).
    """
    if not filename or (season is None and episode is None and absolute_ep is None):
        return True

    fname = filename.strip()
    s_num = int(season) if season else 1
    e_num = int(episode) if episode else 1
    abs_num = int(absolute_ep) if absolute_ep else None

    # 1. Exclusion immédiate des fichiers annexes non-épisodes (OP, ED, PV, Sample, NCED, NCOP, Menu)
    if re.search(r"[\s\-_\[(](?:NC)?(?:OP|ED|PV|TRAILER|SAMPLE|PREVIEW|MENU)\b(?:\d+)?", fname, re.IGNORECASE):
        if not re.search(r"\bOne\s+Piece\b", fname, re.IGNORECASE) or re.search(r"[\s\-_\[(](?:NCOP|NCED|PV|SAMPLE)\b", fname, re.IGNORECASE):
            return False

    # 2. Règle absolue Standard TV : S01E05 ou 1x05
    std_pattern = rf"(?:s0?{s_num}[\.\-_ ]?e0?{e_num}\b|\b{s_num}x0?{e_num}\b)"
    if re.search(std_pattern, fname, re.IGNORECASE):
        return True

    # 3. Règle Saisonnière Anime explicite :
    # "S02 - 05", "Season 2 - 05", "2nd Season - 05", "S2 05", "Part 2 - 05"
    s_marker = rf"(?:s0?{s_num}|season\s*0?{s_num}|{s_num}(?:nd|rd|th|st)\s*season|part\s*0?{s_num})\b"
    if re.search(s_marker, fname, re.IGNORECASE):
        ep_after_s = re.search(s_marker + r"[\s\-_:]*(?:ep(?:isode)?\.?\s*|e)?0*" + str(e_num) + r"(?:v\d+)?(?:[\s\-_\]\).]|$)", fname, re.IGNORECASE)
        if ep_after_s:
            return True

    # 4. Règle Anime Numérotation Absolue ou Épisode Direct :
    cleaned = fname
    # Supprimer les hashes hexadécimaux [ABCDEF01]
    cleaned = re.sub(r"\[[0-9a-fA-F]{6,8}\]", "", cleaned)
    # Supprimer résolutions et specs
    cleaned = re.sub(r"\b(?:2160p|1080p|720p|480p|4k|uhd|fhd|hd)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:x264|x265|h264|h265|hevc|av1|10bit|8bit)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:5\.1|7\.1|2\.0)\b", "", cleaned)
    cleaned = re.sub(r"\b(?:19|20)\d{2}\b", "", cleaned)
    cleaned = re.sub(r"\b(?:aac|flac|ac3|eac3|dts|mp3)\b", "", cleaned, flags=re.IGNORECASE)

    # Si le fichier mentionne explicitement une AUTRE saison (ex: S02 alors qu'on cherche S01), rejeter
    other_season = re.search(r"\b(?:s|season|part)\s*0?(\d+)\b", cleaned, re.IGNORECASE)
    if other_season:
        detected_s = int(other_season.group(1))
        if detected_s != s_num:
            return False

    target_numbers = [e_num]
    if abs_num and abs_num != e_num:
        target_numbers.append(abs_num)

    for target_n in target_numbers:
        pats = [
            rf"[\s\-_]0*{target_n}(?:v\d+)?[\s\-_\.\]\)]",
            rf"\[0*{target_n}(?:v\d+)?\]",
            rf"\(0*{target_n}(?:v\d+)?\)",
            rf"\b(?:ep(?:isode)?\.?|e|#)\s*0*{target_n}(?:v\d+)?\b",
        ]
        for p in pats:
            if re.search(p, cleaned, re.IGNORECASE):
                return True

    return False


class EpisodeMatcher:
    def __init__(self, season=None, episode=None, absolute_ep=None):
        self.season = int(season) if season is not None else None
        self.episode = int(episode) if episode is not None else None
        self.absolute_ep = int(absolute_ep) if absolute_ep is not None else None

    def search(self, filename: str) -> bool:
        return is_target_episode_file(filename, self.season, self.episode, self.absolute_ep)


def _build_ep_pattern(season, episode, absolute_ep=None):
    if season is not None or episode is not None or absolute_ep is not None:
        return EpisodeMatcher(season, episode, absolute_ep)
    return None


def _alldebrid_unlock_magnet(token, m_info, season=None, episode=None):
    ep_pattern = _build_ep_pattern(season, episode)
    links = m_info.get("links") or []
    candidates = []
    for l_item in links:
        fname = l_item.get("filename") or "video.mkv"
        fsize = int(l_item.get("size") or 0)
        raw_link = l_item.get("link") or ""
        if not raw_link:
            continue
        is_vid = fname.lower().endswith(VIDEO_EXTENSIONS) or fsize > 25 * 1024 * 1024
        if not is_vid and len(links) > 1:
            continue
        is_target = bool(ep_pattern and ep_pattern.search(fname))
        candidates.append({
            "filename": fname,
            "filesize": format_size(fsize),
            "filesize_bytes": fsize,
            "raw_link": raw_link,
            "is_target_ep": is_target,
        })
    candidates.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
    unrestricted = []
    for c in candidates[:12]:
        try:
            q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "link": c["raw_link"]})
            u = http_json(f"https://api.alldebrid.com/v4/link/unlock?{q}")
            dl = ((u.get("data") or {}).get("link")) or ""
            if dl:
                unrestricted.append({
                    "filename": c["filename"],
                    "filesize": c["filesize"],
                    "filesize_bytes": c["filesize_bytes"],
                    "download": dl,
                    "is_target_ep": c["is_target_ep"],
                })
        except Exception:
            pass
    return {
        "ready": True,
        "torrent_id": f"ad:{m_info.get('id')}",
        "status": "downloaded",
        "progress": 100,
        "files": unrestricted,
    }


def rd_debrid_magnet(token, magnet, season=None, episode=None, resolve_url=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    pmeta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])
    if not token:
        raise RuntimeError(f"Clé API {pmeta['name']} manquante. Cliquez sur 'Config' en haut à droite.")

    # Si on a un lien direct Torrentio ([RD+], [AD+], [TB+], [DL+], [PM+]), on le résout directement !
    target_resolve = resolve_url or (magnet if magnet.startswith("http") else None)
    if target_resolve:
        try:
            return resolve_torrentio_rd_url(target_resolve)
        except Exception:
            if not magnet.startswith("magnet:"):
                raise

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        up = http_json(f"https://api.alldebrid.com/v4/magnet/upload?{q}", method="POST", data={"magnets[]": magnet})
        magnets = ((up.get("data") or {}).get("magnets")) or []
        if not magnets:
            raise RuntimeError("AllDebrid n'a pas pu ajouter ce magnet.")
        mid = magnets[0].get("id")
        for _ in range(6):
            st = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}&id={mid}")
            m_info = ((st.get("data") or {}).get("magnets")) or {}
            if isinstance(m_info, list):
                m_info = m_info[0] if m_info else {}
            if m_info.get("statusCode") == 4:
                return _alldebrid_unlock_magnet(token, m_info, season, episode)
            if m_info.get("statusCode", 0) > 4:
                raise RuntimeError(f"AllDebrid erreur magnet : {m_info.get('status')}")
            time.sleep(1.0)
        return {
            "ready": False,
            "torrent_id": f"ad:{mid}",
            "status": m_info.get("status", "Téléchargement Cloud AllDebrid"),
            "progress": int(
                (m_info.get("downloaded", 0) / max(1, m_info.get("size", 1))) * 100
            ) if m_info.get("size") else 0,
            "speed": format_size(m_info.get("downloadSpeed", 0)) + "/s",
            "seeders": m_info.get("seeders", 0),
            "files": [],
        }

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://debrid-link.com/api/v2/seedbox/add", method="POST", data={"url": magnet, "async": "true"}, headers=auth)
        val = res.get("value") or {}
        files = val.get("files") or []
        ep_pattern = _build_ep_pattern(season, episode)
        unrestricted = []
        for f in files:
            dl = f.get("downloadUrl")
            fname = f.get("name") or "video.mkv"
            if dl and f.get("downloadPercent") == 100:
                unrestricted.append({
                    "filename": fname,
                    "filesize": format_size(f.get("size", 0)),
                    "filesize_bytes": f.get("size", 0),
                    "download": dl,
                    "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                })
        if unrestricted:
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": f"dl:{val.get('id')}", "status": "downloaded", "progress": 100, "files": unrestricted}
        return {
            "ready": False,
            "torrent_id": f"dl:{val.get('id')}",
            "status": "Mise en cache Debrid-Link",
            "progress": val.get("downloadPercent", 0),
            "speed": format_size(val.get("downloadSpeed", 0)) + "/s",
            "seeders": val.get("peersConnected", 0),
            "files": [],
        }

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/transfer/directdl?{q}", method="POST", data={"src": magnet})
        content = res.get("content") or []
        ep_pattern = _build_ep_pattern(season, episode)
        unrestricted = []
        for c in content:
            dl = c.get("stream_link") or c.get("link")
            fname = (c.get("path") or "video.mkv").split("/")[-1]
            if dl and fname.lower().endswith(VIDEO_EXTENSIONS):
                unrestricted.append({
                    "filename": fname,
                    "filesize": format_size(c.get("size", 0)),
                    "filesize_bytes": c.get("size", 0),
                    "download": dl,
                    "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                })
        if unrestricted:
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": "pm_direct", "status": "downloaded", "progress": 100, "files": unrestricted}
        raise RuntimeError("Ce magnet n'est pas encore en cache instantané sur Premiumize.")

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        added = http_json("https://api.torbox.app/v1/api/torrents/createtorrent", method="POST", data={"magnet": magnet}, headers=auth)
        tid = (added.get("data") or {}).get("torrent_id")
        if not tid:
            raise RuntimeError(added.get("detail") or "TorBox n'a pas pu ajouter ce magnet.")
        return rd_check_torrent(token, f"tb:{tid}", season, episode, provider="torbox")

    if prov == "megadebrid":
        md_tok = token
        if ":" in token:
            login, pw = token.split(":", 1)
            q = urllib.parse.urlencode({"form": "connexion", "login": login, "password": pw})
            c_res = http_json(f"https://www.mega-debrid.eu/index.php?{q}")
            md_tok = c_res.get("token", "")
        if not magnet.startswith("magnet:"):
            q = urllib.parse.urlencode({"form": "debrid", "token": md_tok})
            d_res = http_json(f"https://www.mega-debrid.eu/index.php?{q}", method="POST", data={"link": magnet})
            dl = d_res.get("debridLink", "")
            if dl:
                fname = d_res.get("filename") or urllib.parse.unquote(dl.split("/")[-1].split("?")[0])
                return {
                    "ready": True,
                    "torrent_id": "md_direct",
                    "status": "downloaded",
                    "progress": 100,
                    "files": [{"filename": fname, "filesize": "Prêt (Mega-Debrid)", "filesize_bytes": 0, "download": dl, "is_target_ep": True}],
                }
        raise RuntimeError("Mega-Debrid débride les liens hébergeurs directs. Pour le streaming torrent P2P instantané, utilisez Real-Debrid, AllDebrid, TorBox, Debrid-Link ou Premiumize.")

    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"

    added = http_json(f"{base}/torrents/addMagnet", method="POST", data={"magnet": magnet}, headers=auth)
    torrent_id = added["id"]

    info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
    files = info.get("files", [])

    video_files = [
        f for f in files
        if f.get("path", "").lower().endswith(VIDEO_EXTENSIONS) and f.get("bytes", 0) > 25 * 1024 * 1024
    ]

    selected_ids = []
    if video_files:
        selected_ids = [str(f["id"]) for f in video_files]
    elif files:
        largest = max(files, key=lambda x: x.get("bytes", 0))
        selected_ids = [str(largest["id"])]

    selection = ",".join(selected_ids) if selected_ids else "all"
    if files:
        http_json(f"{base}/torrents/selectFiles/{torrent_id}", method="POST", data={"files": selection}, headers=auth)

    for _ in range(8):
        info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
        status = info.get("status")
        if status == "downloaded":
            break
        if status in ("error", "magnet_error", "virus", "dead"):
            raise RuntimeError(f"Real-Debrid a refusé ce torrent (statut : {status})")
        time.sleep(1.0)

    if info.get("status") != "downloaded":
        return {
            "ready": False,
            "torrent_id": torrent_id,
            "status": info.get("status"),
            "progress": info.get("progress", 0),
            "speed": format_size(info.get("speed", 0)) + "/s",
            "seeders": info.get("seeders", 0),
            "files": [],
        }

    return unrestrict_torrent_links(token, info, season, episode)


def rd_check_torrent(token, torrent_id, season=None, episode=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    tid_str = str(torrent_id or "")

    if tid_str.startswith("ad:") or prov == "alldebrid":
        mid = tid_str.split(":", 1)[-1]
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "id": mid})
        st = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}")
        m_info = ((st.get("data") or {}).get("magnets")) or {}
        if isinstance(m_info, list):
            m_info = m_info[0] if m_info else {}
        if m_info.get("statusCode") == 4:
            return _alldebrid_unlock_magnet(token, m_info, season, episode)
        return {
            "ready": False,
            "torrent_id": f"ad:{mid}",
            "status": m_info.get("status", "Téléchargement AllDebrid"),
            "progress": int((m_info.get("downloaded", 0) / max(1, m_info.get("size", 1))) * 100) if m_info.get("size") else 0,
            "speed": format_size(m_info.get("downloadSpeed", 0)) + "/s",
            "seeders": m_info.get("seeders", 0),
            "files": [],
        }

    if tid_str.startswith("tb:") or prov == "torbox":
        raw_tid = tid_str.split(":", 1)[-1]
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json(f"https://api.torbox.app/v1/api/torrents/mylist?bypass_cache=true&id={raw_tid}", headers=auth)
        t_info = res.get("data") or {}
        if isinstance(t_info, list):
            t_info = t_info[0] if t_info else {}
        if t_info.get("download_present") and t_info.get("files"):
            ep_pattern = _build_ep_pattern(season, episode)
            unrestricted = []
            for f in t_info.get("files") or []:
                fname = f.get("short_name") or f.get("name") or "video.mkv"
                if not fname.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                fid = f.get("id", 0)
                dl_res = http_json(f"https://api.torbox.app/v1/api/torrents/requestdl?token={token}&torrent_id={raw_tid}&file_id={fid}", headers=auth)
                dl_url = dl_res.get("data") or ""
                if dl_url:
                    unrestricted.append({
                        "filename": fname,
                        "filesize": format_size(f.get("size", 0)),
                        "filesize_bytes": f.get("size", 0),
                        "download": dl_url,
                        "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                    })
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": f"tb:{raw_tid}", "status": "downloaded", "progress": 100, "files": unrestricted}
        return {
            "ready": False,
            "torrent_id": f"tb:{raw_tid}",
            "status": t_info.get("download_state", "Téléchargement TorBox"),
            "progress": int((t_info.get("progress") or 0) * 100),
            "speed": format_size(t_info.get("download_speed", 0)) + "/s",
            "seeders": t_info.get("seeds", 0),
            "files": [],
        }

    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"
    info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
    if info.get("status") != "downloaded":
        return {
            "ready": False,
            "torrent_id": torrent_id,
            "status": info.get("status"),
            "progress": info.get("progress", 0),
            "speed": format_size(info.get("speed", 0)) + "/s",
            "seeders": info.get("seeders", 0),
            "files": [],
        }
    return unrestrict_torrent_links(token, info, season, episode)


def unrestrict_torrent_links(token, info, season=None, episode=None):
    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"
    unrestricted = []

    ep_pattern = _build_ep_pattern(season, episode)

    for link in info.get("links", []):
        try:
            u = http_json(f"{base}/unrestrict/link", method="POST", data={"link": link}, headers=auth)
            fname = u.get("filename", "video.mkv")
            is_target_ep = bool(ep_pattern and ep_pattern.search(fname))
            unrestricted.append({
                "filename": fname,
                "filesize": format_size(u.get("filesize", 0)),
                "filesize_bytes": u.get("filesize", 0),
                "download": u.get("download"),
                "is_target_ep": is_target_ep,
            })
        except Exception:
            pass

    # Si un épisode précis correspond dans un pack, on le place en premier
    unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))

    return {
        "ready": True,
        "torrent_id": info.get("id"),
        "status": "downloaded",
        "progress": 100,
        "files": unrestricted,
    }


# ==========================================
# LECTEUR MPV
# ==========================================
def find_mpv():
    cfg = load_config()
    candidates = [
        cfg.get("mpv_path"),
        shutil.which("mpv"),
        shutil.which("iina-cli"),
        shutil.which("iina"),
        # Chemins standards macOS
        "/opt/homebrew/bin/mpv",
        "/usr/local/bin/mpv",
        "/Applications/mpv.app/Contents/MacOS/mpv",
        "/Applications/IINA.app/Contents/MacOS/iina-cli",
        "/Applications/IINA.app/Contents/MacOS/IINA",
        "/Applications/VLC.app/Contents/MacOS/VLC",
        # Chemins standards Windows
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        str(Path.home() / "scoop" / "shims" / "mpv.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "mpv.exe"),
        str(Path.home() / "Downloads" / "mpv-x86_64-20260517-git-059bc7025b" / "mpv.exe"),
    ]
    # Recherche dynamique dans Downloads/mpv*
    dl_dir = Path.home() / "Downloads"
    if dl_dir.exists():
        for p in sorted(dl_dir.glob("mpv*/mpv.exe"), reverse=True):
            candidates.append(str(p))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def find_ffmpeg():
    """Détecte l'exécutable FFmpeg sur la machine (WinGet, PATH standard, macOS Homebrew, Linux)."""
    path = shutil.which("ffmpeg")
    if path and os.path.exists(path):
        return path
    candidates = [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    # Recherche dynamique dans WinGet Packages
    wg_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if wg_dir.exists():
        for p in wg_dir.glob("**/ffmpeg.exe"):
            if p.is_file():
                candidates.append(str(p))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def spawn_on_user_desktop(args):
    """Lance un processus détaché (cross-platform : Windows via WinSta0\\Default, macOS/Linux via start_new_session)."""
    if sys.platform != "win32":
        proc = subprocess.Popen(args, start_new_session=True)
        return proc.pid
    import ctypes
    import ctypes.wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("lpReserved", ctypes.wintypes.LPWSTR),
            ("lpDesktop", ctypes.wintypes.LPWSTR),
            ("lpTitle", ctypes.wintypes.LPWSTR),
            ("dwX", ctypes.wintypes.DWORD),
            ("dwY", ctypes.wintypes.DWORD),
            ("dwXSize", ctypes.wintypes.DWORD),
            ("dwYSize", ctypes.wintypes.DWORD),
            ("dwXCountChars", ctypes.wintypes.DWORD),
            ("dwYCountChars", ctypes.wintypes.DWORD),
            ("dwFillAttribute", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("wShowWindow", ctypes.wintypes.WORD),
            ("cbReserved2", ctypes.wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", ctypes.wintypes.HANDLE),
            ("hStdOutput", ctypes.wintypes.HANDLE),
            ("hStdError", ctypes.wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", ctypes.wintypes.HANDLE),
            ("hThread", ctypes.wintypes.HANDLE),
            ("dwProcessId", ctypes.wintypes.DWORD),
            ("dwThreadId", ctypes.wintypes.DWORD),
        ]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpDesktop = "WinSta0\\Default"
    si.dwFlags = 1  # STARTF_USESHOWWINDOW
    si.wShowWindow = 1  # SW_SHOWNORMAL
    pi = PROCESS_INFORMATION()

    cmd_line = subprocess.list2cmdline(args)
    cmd_buf = ctypes.create_unicode_buffer(cmd_line)
    CREATE_NEW_CONSOLE = 0x00000010
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    ok = ctypes.windll.kernel32.CreateProcessW(
        None,
        cmd_buf,
        None,
        None,
        False,
        CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB,
        None,
        None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        ok = ctypes.windll.kernel32.CreateProcessW(
            None,
            cmd_buf,
            None,
            None,
            False,
            CREATE_NEW_CONSOLE,
            None,
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
    if not ok:
        raise RuntimeError(f"CreateProcessW a échoué (code {ctypes.GetLastError()})")

    ctypes.windll.kernel32.CloseHandle(pi.hProcess)
    ctypes.windll.kernel32.CloseHandle(pi.hThread)
    return pi.dwProcessId


def build_series_playlist_items(
    imdb_id,
    season,
    start_ep,
    first_url,
    series_name="",
    year="",
    poster="",
    pref_hash="",
    first_filename="",
):
    """Construit la playlist M3U de la saison : l'épisode en cours (URL directe RD) + les épisodes suivants via /api/auto-stream."""
    s = int(season or 1)
    ep0 = int(start_ep or 1)
    label_base = (series_name or "Série").strip()

    if imdb_id and first_url:
        with AUTO_STREAM_LOCK:
            AUTO_STREAM_CACHE[(imdb_id, s, ep0)] = {
                "download": first_url,
                "filename": first_filename or f"{label_base}.S{s:02d}E{ep0:02d}",
                "ts": time.time(),
            }

    videos = []
    if imdb_id:
        try:
            meta = get_series_meta(imdb_id)
            if not series_name and meta.get("name"):
                label_base = meta["name"]
            for v in meta.get("videos") or []:
                if int(v.get("season") or 0) == s:
                    ep_num = int(v.get("episode") or v.get("number") or 0)
                    if ep_num > 0:
                        videos.append({
                            "episode": ep_num,
                            "name": v.get("name") or v.get("title") or f"Épisode {ep_num}",
                        })
        except Exception:
            videos = []

    videos.sort(key=lambda x: x["episode"])
    ep0_name = next((v["name"] for v in videos if v["episode"] == ep0), "")
    future_eps = [v for v in videos if v["episode"] > ep0]

    if not future_eps and imdb_id:
        future_eps = [
            {"episode": ep_num, "name": f"Épisode {ep_num}"}
            for ep_num in range(ep0 + 1, min(ep0 + 15, 25))
        ]

    first_title = f"{label_base} — S{s:02d}E{ep0:02d}" + (f" — {ep0_name}" if ep0_name else "")
    items = [{"title": first_title, "url": first_url}]

    for v in future_eps:
        ep_num = v["episode"]
        ep_name = v["name"]
        qs = urllib.parse.urlencode({
            "imdb_id": imdb_id,
            "season": s,
            "episode": ep_num,
            "name": label_base,
            "year": year or "",
            "poster": poster or "",
            "pref_hash": pref_hash or "",
        })
        items.append({
            "title": f"{label_base} — S{s:02d}E{ep_num:02d} — {ep_name}",
            "url": f"http://127.0.0.1:{PORT}/api/auto-stream?{qs}",
        })

    return items


def resolve_auto_stream_episode(params):
    """Résout à la demande l'URL directe Real-Debrid d'un épisode de série lorsque MPV passe au suivant."""
    cfg = load_config()
    token = cfg.get("rd_token", "").strip()
    if not token:
        raise RuntimeError("Token API Real-Debrid manquant.")

    imdb_id = params.get("imdb_id", "").strip()
    s = int(params.get("season") or 1)
    ep = int(params.get("episode") or 1)
    name = params.get("name") or "Série"
    year = params.get("year") or ""
    poster = params.get("poster") or ""
    pref_hash = (params.get("pref_hash") or "").strip().lower()

    cache_key = (imdb_id, s, ep)
    with AUTO_STREAM_LOCK:
        cached = AUTO_STREAM_CACHE.get(cache_key)
        if cached and (time.time() - cached["ts"] < 1800):
            if imdb_id:
                record_history({
                    "id": imdb_id,
                    "name": name,
                    "type": "series",
                    "year": year,
                    "poster": poster,
                    "season": s,
                    "episode": ep,
                    "filename": cached.get("filename", f"{name} S{s:02d}E{ep:02d}"),
                })
            return cached["download"]

    torrents = search_torrentio(imdb_id, "series", s, ep, rd_token=token, query_title=name) if imdb_id else []
    if not torrents:
        raise RuntimeError(f"Aucun flux trouvé pour {name} S{s:02d}E{ep:02d}.")

    torrents.sort(
        key=lambda c: (
            1 if (pref_hash and (c.get("info_hash") or "").lower() == pref_hash) else 0,
            score_torrent_for_one_click(c),
        ),
        reverse=True,
    )

    last_err = None
    for cand in torrents[:10]:
        try:
            res = rd_debrid_magnet(
                token,
                cand.get("magnet", ""),
                season=s,
                episode=ep,
                resolve_url=cand.get("resolve_url", ""),
            )
            if res.get("ready") and res.get("files"):
                target_files = [f for f in res["files"] if f.get("is_target_ep")]
                if len(res["files"]) > 1 and not target_files:
                    continue
                target_file = target_files[0] if target_files else res["files"][0]
                dl_url = target_file["download"]
                fname = target_file.get("filename", f"{name} S{s:02d}E{ep:02d}")
                with AUTO_STREAM_LOCK:
                    AUTO_STREAM_CACHE[cache_key] = {
                        "download": dl_url,
                        "filename": fname,
                        "ts": time.time(),
                    }
                if imdb_id:
                    record_history({
                        "id": imdb_id,
                        "name": name,
                        "type": "series",
                        "year": year,
                        "poster": poster,
                        "season": s,
                        "episode": ep,
                        "filename": fname,
                    })
                return dl_url
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Impossible de résoudre S{s:02d}E{ep:02d} ({last_err or 'aucun flux RD+ prêt'}).")


def prefetch_next_episode(imdb_id, season, episode=None, next_ep=None, name="", year="", poster="", pref_hash=""):
    """Pré-résout en arrière-plan le lien du débrideur pour l'épisode suivant pour une transition en 0 seconde."""
    target_ep = int(next_ep) if next_ep is not None else (int(episode) + 1 if episode is not None else 0)
    if not imdb_id or not season or target_ep <= 0:
        return

    def _worker():
        time.sleep(4.0)
        try:
            resolve_auto_stream_episode({
                "imdb_id": imdb_id,
                "season": int(season),
                "episode": target_ep,
                "name": name or "Série",
                "year": year or "",
                "poster": poster or "",
                "pref_hash": pref_hash or "",
            })
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def query_mpv_ipc(sock_path, prop_name):
    if not sock_path:
        return None
    payload = json.dumps({"command": ["get_property", prop_name]}) + "\n"
    if sys.platform == "win32":
        try:
            with open(sock_path, "r+b", buffering=0) as f:
                f.write(payload.encode("utf-8"))
                line = f.readline()
                if line:
                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                    if msg.get("error") == "success":
                        return msg.get("data")
        except Exception:
            return None
        return None

    import socket
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(sock_path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            s.connect(sock_path)
            s.sendall(payload.encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            for line in data.decode("utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("error") == "success":
                    return msg.get("data")
    except Exception:
        return None
    return None


def monitor_ipc_playback(proc, media_ctx):
    if not media_ctx or not media_ctx.get("id"):
        return
    base_ep = int(media_ctx.get("episode") or 1)
    season = int(media_ctx.get("season") or 1) if media_ctx.get("type") == "series" else None
    time.sleep(2.0)
    while proc.poll() is None:
        pos = query_mpv_ipc(IPC_SOCK_PATH, "time-pos")
        dur = query_mpv_ipc(IPC_SOCK_PATH, "duration")
        pl_pos = query_mpv_ipc(IPC_SOCK_PATH, "playlist-pos")
        if isinstance(pos, (int, float)) and isinstance(dur, (int, float)) and dur > 30 and pos > 3:
            cur_ep = base_ep + int(pl_pos) if (season and isinstance(pl_pos, int) and pl_pos >= 0) else ( base_ep if season else None )
            entry = {
                "id": media_ctx["id"],
                "name": media_ctx.get("name", "KINO"),
                "type": media_ctx.get("type", "movie"),
                "year": media_ctx.get("year", ""),
                "poster": media_ctx.get("poster", ""),
                "season": season,
                "episode": cur_ep,
                "filename": media_ctx.get("filename", ""),
                "position": int(pos),
                "duration": int(dur),
            }
            try:
                record_history(entry)
            except Exception:
                pass
        time.sleep(3.0)


def monitor_mpv_lifecycle(pid):
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h_proc = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if h_proc:
            ctypes.windll.kernel32.WaitForSingleObject(h_proc, 0xFFFFFFFF)
            ctypes.windll.kernel32.CloseHandle(h_proc)
    if WINDOW_ACTION_CALLBACK:
        try:
            WINDOW_ACTION_CALLBACK("show")
        except Exception:
            pass


def launch_mpv(url: str, title: str = "", playlist_items=None, media_ctx=None, start_sec: int = 0):
    mpv_bin = find_mpv()
    if not mpv_bin:
        raise RuntimeError("Aucun lecteur externe compatible (mpv, IINA ou VLC) n'a été trouvé.")

    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    alang = "eng,ja,jpn,fre,fra,fr" if pref_lang == "vostfr" else "fre,fra,fr,eng"
    slang = "fre,fra,fr,eng"

    safe_title = (title or "KINO").replace('"', "'")
    is_iina = "iina" in mpv_bin.lower()
    is_vlc = "vlc" in mpv_bin.lower()

    if sys.platform != "win32":
        try:
            if os.path.exists(IPC_SOCK_PATH):
                os.remove(IPC_SOCK_PATH)
        except Exception:
            pass

    target_media = url
    has_playlist = bool(playlist_items and len(playlist_items) > 1)
    if has_playlist:
        lines = ["#EXTM3U"]
        for item in playlist_items:
            t = (item.get("title") or "KINO").replace("\n", " ").replace("\r", " ").strip()
            u = (item.get("url") or "").strip()
            if u:
                lines.append(f"#EXTINF:-1,{t}")
                lines.append(u)
        PLAYLIST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target_media = str(PLAYLIST_FILE)

    hdr_mode = cfg.get("hdr_mode", "sdr_pref")
    audio_mode = cfg.get("audio_mode", "voice_boost")
    fname_ctx = (media_ctx.get("filename", "") if isinstance(media_ctx, dict) else "") or ""
    check_hdr_str = f"{title} {fname_ctx} {url}"
    is_hdr_media = bool(re.search(r"\b(hdr|hdr10|hdr10\+|dv|dovi|dolby[\s\.\-]*vision|hlg)\b", check_hdr_str, re.IGNORECASE))

    ext_sub_files = []
    if isinstance(media_ctx, dict) and media_ctx.get("id"):
        try:
            ext_sub_files = download_top_subtitles_for_mpv(
                media_ctx["id"],
                media_ctx.get("type", "movie"),
                media_ctx.get("season", 1),
                media_ctx.get("episode", 1),
            )
        except Exception:
            ext_sub_files = []
    sub_sep = ";" if sys.platform == "win32" else ":"

    mpv_input_conf = None
    if sys.platform != "win32":
        try:
            mpv_input_conf = "/tmp/kino_mpv_input.conf"
            Path(mpv_input_conf).write_text(
                's seek 85 exact ; show-text "⏭ Intro passée (+85s)"\n'
                'S seek 85 exact ; show-text "⏭ Intro passée (+85s)"\n',
                encoding="utf-8",
            )
        except Exception:
            mpv_input_conf = None

    if is_iina:
        # Utiliser iina-cli si le chemin pointe vers le binaire IINA brut
        if mpv_bin.endswith("/IINA"):
            cli_cand = Path(mpv_bin).parent / "iina-cli"
            if cli_cand.exists():
                mpv_bin = str(cli_cand)
        if sys.platform == "darwin" and hdr_mode in ("sdr_pref", "hdr_boost"):
            try:
                subprocess.run(
                    ["defaults", "write", "com.colliderli.iina", "enableToneMapping", "-bool", "true"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                )
            except Exception:
                pass
        args = [
            mpv_bin,
            "--no-stdin",
            "--keep-running",
            "--mpv-hwdec=videotoolbox",
            f"--mpv-input-ipc-server={IPC_SOCK_PATH}",
            f"--mpv-alang={alang}",
            f"--mpv-slang={slang}",
        ]
        if mpv_input_conf:
            args.append(f"--mpv-input-conf={mpv_input_conf}")
        if audio_mode == "voice_boost":
            args.append("--mpv-af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]")
        if ext_sub_files:
            args.append(f"--mpv-sub-files={sub_sep.join(ext_sub_files)}")
        if hdr_mode != "hdr_native":
            args.extend([
                "--mpv-tone-mapping=bt.2446a",
                "--mpv-hdr-compute-peak=yes",
            ])
            if hdr_mode == "hdr_boost":
                args.extend([
                    "--mpv-gamma=9",
                    "--mpv-brightness=3",
                    "--mpv-contrast=2",
                ])
            elif is_hdr_media:
                args.extend([
                    "--mpv-gamma=6",
                    "--mpv-brightness=2",
                ])
        if start_sec and int(start_sec) > 5:
            args.append(f"--mpv-start={int(start_sec)}")
        if not has_playlist:
            args.append(f"--mpv-force-media-title={safe_title}")
        args.append(target_media)
    elif is_vlc:
        args = [mpv_bin, target_media]
    else:
        cfg_dir = Path(mpv_bin).parent / "portable_config"
        args = [mpv_bin]
        if cfg_dir.exists():
            args.append(f"--config-dir={cfg_dir}")
        if sys.platform == "darwin":
            args.append("--hwdec=videotoolbox")
        args.append(f"--input-ipc-server={IPC_SOCK_PATH}")
        if mpv_input_conf:
            args.append(f"--input-conf={mpv_input_conf}")
        args.extend([
            f"--alang={alang}",
            f"--slang={slang}",
            "--no-border",
            "--border=no",
            "--title=KINO",
            "--force-window=immediate",
        ])
        if audio_mode == "voice_boost":
            args.append("--af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]")
        if ext_sub_files:
            args.append(f"--sub-files={sub_sep.join(ext_sub_files)}")
        if hdr_mode != "hdr_native":
            args.extend([
                "--tone-mapping=bt.2446a",
                "--hdr-compute-peak=yes",
            ])
            if hdr_mode == "hdr_boost":
                args.extend([
                    "--gamma=9",
                    "--brightness=3",
                    "--contrast=2",
                ])
            elif is_hdr_media:
                args.extend([
                    "--gamma=6",
                    "--brightness=2",
                ])
        if start_sec and int(start_sec) > 5:
            args.append(f"--start={int(start_sec)}")

        if GET_WINDOW_GEOMETRY:
            try:
                geo = GET_WINDOW_GEOMETRY()
                if geo:
                    args.append(f"--geometry={geo['width']}x{geo['height']}+{geo['x']}+{geo['y']}")
            except Exception:
                pass

        if not has_playlist:
            args.append(f"--force-media-title={safe_title}")
        args.append(target_media)

    if WINDOW_ACTION_CALLBACK:
        try:
            WINDOW_ACTION_CALLBACK("hide")
        except Exception:
            pass

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/IM", "mpv.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = spawn_on_user_desktop(args)
        threading.Thread(target=monitor_mpv_lifecycle, args=(pid,), daemon=True).start()
        return {"mpv": mpv_bin, "pid": pid, "playlist_count": len(playlist_items) if playlist_items else 1}
    else:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if media_ctx:
            threading.Thread(target=monitor_ipc_playback, args=(proc, media_ctx), daemon=True).start()
        def _wait():
            try:
                proc.wait()
            except Exception:
                pass
            if WINDOW_ACTION_CALLBACK:
                try:
                    WINDOW_ACTION_CALLBACK("show")
                except Exception:
                    pass
        threading.Thread(target=_wait, daemon=True).start()
        return {"mpv": mpv_bin, "pid": proc.pid, "playlist_count": len(playlist_items) if playlist_items else 1}


# ==========================================
# TÉLÉCHARGEMENT PC EN ARRIÈRE-PLAN
# ==========================================
def start_background_download(url, filename, dest_dir):
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
    dl_id = f"{int(time.time() * 1000)}_{safe_name}"
    dest_path = Path(dest_dir)
    dest_path.mkdir(parents=True, exist_ok=True)
    filepath = dest_path / safe_name

    with DOWNLOADS_LOCK:
        DOWNLOADS[dl_id] = {
            "id": dl_id,
            "filename": safe_name,
            "path": str(filepath),
            "progress": 0,
            "downloaded": "0 B",
            "total": "?",
            "speed": "0 B/s",
            "status": "downloading",
            "cancel": False,
        }

    def worker():
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                start_t = time.time()
                with open(filepath, "wb") as f:
                    while True:
                        with DOWNLOADS_LOCK:
                            if DOWNLOADS[dl_id].get("cancel"):
                                break
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.time() - start_t, 0.1)
                        spd = downloaded / elapsed
                        pct = round(downloaded * 100 / total, 1) if total else 0
                        with DOWNLOADS_LOCK:
                            DOWNLOADS[dl_id].update({
                                "progress": pct,
                                "downloaded": format_size(downloaded),
                                "total": format_size(total) if total else "?",
                                "speed": f"{format_size(spd)}/s",
                            })
            with DOWNLOADS_LOCK:
                if DOWNLOADS[dl_id].get("cancel"):
                    DOWNLOADS[dl_id]["status"] = "cancelled"
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    DOWNLOADS[dl_id]["status"] = "completed"
                    DOWNLOADS[dl_id]["progress"] = 100
        except Exception as e:
            with DOWNLOADS_LOCK:
                DOWNLOADS[dl_id]["status"] = f"error: {e}"

    threading.Thread(target=worker, daemon=True).start()
    return dl_id


# ==========================================
# INTERFACE WEB MONOCHROME SOBRE — KINO
# ==========================================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KINO</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='4' fill='%2309090b'/%3E%3Crect x='3' y='3' width='26' height='26' rx='2' fill='none' stroke='%23fafafa' stroke-width='2'/%3E%3Cpath d='M10 8v16M10 16l11-8v16L10 16z' fill='%23fafafa' stroke='%23fafafa' stroke-width='1.5' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #09090b;
    --surface: #111113;
    --surface-2: #18181b;
    --border: #27272a;
    --border-hover: #52525b;
    --text: #fafafa;
    --muted: #a1a1aa;
    --dim: #71717a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; scrollbar-width: none !important; -ms-overflow-style: none !important; }
  html, body {
    scrollbar-width: none !important;
    -ms-overflow-style: none !important;
    overflow-y: auto;
  }
  *::-webkit-scrollbar, ::-webkit-scrollbar {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
    background: transparent !important;
  }
  ::-webkit-scrollbar-button {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
  }
  ::-webkit-scrollbar-thumb, ::-webkit-scrollbar-track {
    background: transparent !important;
    border: none !important;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding-bottom: 64px;
    -webkit-font-smoothing: antialiased;
    border: none !important;
    outline: none !important;
  }
  header {
    background: var(--bg);
    border-top: none !important;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    padding: 14px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 50;
    flex-wrap: wrap;
    gap: 12px;
  }
  /* Header Toolbar & Controls redesign */
  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .header-user-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    padding: 5px 12px 5px 9px;
    font-size: 0.77rem;
    cursor: pointer;
    transition: all 0.18s ease;
    user-select: none;
  }
  .header-user-pill:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.18);
    transform: translateY(-1px);
  }
  .user-status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 8px rgba(34, 197, 94, 0.65);
    flex-shrink: 0;
  }
  .user-badge-exp {
    font-size: 0.68rem;
    font-weight: 700;
    color: #4ade80;
    background: rgba(34, 197, 94, 0.12);
    padding: 1px 7px;
    border-radius: 999px;
    border: 1px solid rgba(34, 197, 94, 0.25);
    letter-spacing: 0.02em;
    margin-left: 2px;
  }
  .header-v-divider {
    width: 1px;
    height: 18px;
    background: rgba(255, 255, 255, 0.08);
    margin: 0 2px;
  }
  .header-tool-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 11px;
    font-size: 0.78rem;
    font-weight: 500;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.08);
    color: var(--text);
    cursor: pointer;
    transition: all 0.15s ease;
    user-select: none;
  }
  .header-tool-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.2);
    transform: translateY(-1px);
  }
  .header-tool-btn:active {
    transform: scale(0.97);
  }
  .header-btn-torrent {
    background: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.12);
  }
  .header-action-group {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 3px;
  }
  .header-action-group .header-tool-btn {
    border: none;
    background: transparent;
    padding: 5px 9px;
    border-radius: 6px;
  }
  .header-action-group .header-tool-btn:hover {
    background: rgba(255, 255, 255, 0.08);
  }
  .header-menu-wrap {
    position: relative;
  }
  .header-btn-settings {
    gap: 7px;
  }
  .header-btn-settings .chevron-icon {
    opacity: 0.6;
    transition: transform 0.2s;
  }
  .header-menu-wrap.active .header-btn-settings .chevron-icon {
    transform: rotate(180deg);
  }
  .header-dropdown-menu {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    min-width: 260px;
    background: rgba(18, 18, 22, 0.96);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 12px;
    padding: 6px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.7), 0 0 0 1px rgba(255, 255, 255, 0.05);
    z-index: 100;
    display: none;
    flex-direction: column;
    gap: 3px;
    animation: menuFadeIn 0.15s ease-out;
  }
  @keyframes menuFadeIn {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .header-dropdown-item {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.15s ease;
    user-select: none;
  }
  .header-dropdown-item:hover {
    background: rgba(255, 255, 255, 0.07);
  }
  .header-dropdown-item .hdi-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 22px;
    height: 22px;
    color: var(--muted);
  }
  .header-dropdown-item:hover .hdi-icon {
    color: var(--text);
  }
  .header-dropdown-item .hdi-content {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .header-dropdown-item .hdi-title {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text);
  }
  .header-dropdown-item .hdi-desc {
    font-size: 0.7rem;
    color: var(--muted);
  }
  .header-dropdown-divider {
    height: 1px;
    background: rgba(255, 255, 255, 0.08);
    margin: 4px 6px;
  }
  @media (max-width: 960px) {
    .header-action-group .btn-text,
    .header-btn-settings .btn-text {
      display: none;
    }
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--text);
    user-select: none;
    cursor: pointer;
  }
  .logo-mark {
    width: 26px;
    height: 26px;
    display: block;
    flex-shrink: 0;
  }
  .logo-wordmark {
    font-size: 0.96rem;
    font-weight: 700;
    letter-spacing: 0.26em;
    color: var(--text);
  }
  .logo-divider {
    width: 1px;
    height: 14px;
    background: var(--border);
  }
  .logo-sub {
    font-size: 0.72rem;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--dim);
  }
  .container { max-width: 1200px; margin: 24px auto; padding: 0 20px; }
  .search-box {
    display: flex;
    gap: 8px;
    background: var(--surface);
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    flex-wrap: wrap;
  }
  select, input, button {
    font-family: inherit;
    font-size: 0.86rem;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    padding: 9px 12px;
    outline: none;
    transition: border-color 0.15s, background 0.15s;
  }
  input:focus, select:focus { border-color: var(--text); }
  .search-box input { flex: 1; min-width: 240px; }

  .btn {
    cursor: pointer;
    font-weight: 500;
    background: var(--text);
    color: var(--bg);
    border: 1px solid var(--text);
    padding: 8px 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    text-decoration: none;
    white-space: nowrap;
  }
  .btn:hover { background: #e4e4e7; border-color: #e4e4e7; }
  .btn:disabled { opacity: 0.5; cursor: default; }

  .btn-secondary {
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover {
    background: var(--border);
    border-color: var(--border-hover);
  }

  /* Suggestions de recherche instantanée FTS5 */
  .search-dropdown {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    right: 0;
    background: rgba(17, 17, 19, 0.98);
    backdrop-filter: blur(20px);
    border: 1px solid var(--border);
    border-radius: 8px;
    z-index: 100;
    max-height: 420px;
    overflow-y: auto;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.85);
  }
  .search-suggest-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    cursor: pointer;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: background 0.12s;
  }
  .search-suggest-item:last-child { border-bottom: none; }
  .search-suggest-item:hover, .search-suggest-item.selected {
    background: rgba(255, 255, 255, 0.08);
  }
  .search-suggest-poster {
    width: 36px;
    height: 52px;
    object-fit: cover;
    border-radius: 4px;
    background: #18181b;
    flex-shrink: 0;
  }
  .search-suggest-info {
    flex: 1;
    overflow: hidden;
  }
  .search-suggest-title {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .search-suggest-meta {
    font-size: 0.74rem;
    color: var(--muted);
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 3px;
  }

  /* Torrent recommandé & Filtres rapides */
  .torrent-item.is-recommended {
    border-color: rgba(124, 58, 237, 0.5) !important;
    background: linear-gradient(90deg, rgba(124, 58, 237, 0.07), transparent) !important;
    box-shadow: 0 0 16px rgba(124, 58, 237, 0.12);
  }
  .badge-recommended {
    background: linear-gradient(135deg, #7c3aed, #4f46e5) !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    border: none !important;
    box-shadow: 0 0 10px rgba(124, 58, 237, 0.45);
  }
  .torrent-quick-chips {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 10px;
  }
  .quick-chip {
    padding: 4px 10px;
    font-size: 0.76rem;
    border-radius: 9999px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
    cursor: pointer;
    transition: all 0.15s;
    user-select: none;
  }
  .quick-chip:hover {
    color: var(--text);
    border-color: var(--border-hover);
  }
  .quick-chip.active {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
    font-weight: 600;
  }

  /* Modal Raccourcis Clavier */
  .shortcuts-modal {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(8px);
    z-index: 250;
    align-items: center;
    justify-content: center;
  }
  .shortcuts-content {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    max-width: 580px;
    width: 90%;
    padding: 24px;
    box-shadow: 0 20px 50px rgba(0,0,0,0.9);
  }
  .shortcuts-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px 20px;
    margin-top: 16px;
  }
  .shortcut-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.82rem;
  }
  kbd {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 0.75rem;
    font-family: inherit;
    color: var(--text);
  }

  .nav-tabs {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
    flex-wrap: nowrap;
  }
  .nav-tabs-primary {
    display: flex;
    align-items: center;
    gap: 5px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .nav-tabs-primary::-webkit-scrollbar { display: none; }
  .nav-tab {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 7px 14px;
    font-size: 0.83rem;
    font-weight: 500;
    border-radius: 8px;
    cursor: pointer;
    color: var(--muted);
    background: transparent;
    border: 1px solid transparent;
    transition: all 0.15s ease;
    white-space: nowrap;
    user-select: none;
  }
  .nav-tab:hover {
    color: var(--text);
    background: rgba(255, 255, 255, 0.05);
  }
  .nav-tab.active {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
    font-weight: 600;
  }
  .nav-badge {
    font-size: 0.72rem;
    font-weight: 600;
    opacity: 0.85;
    margin-left: 2px;
  }

  /* Sous-navigation Bibliothèque */
  .library-subnav {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 12px;
    padding: 3px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
    border-radius: 9px;
    width: fit-content;
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
    animation: fadeInSub 0.15s ease-out;
  }
  @keyframes fadeInSub {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .library-subnav::-webkit-scrollbar { display: none; }
  .lib-subtab {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    font-size: 0.79rem;
    font-weight: 500;
    border-radius: 6px;
    cursor: pointer;
    color: var(--muted);
    background: transparent;
    border: none;
    transition: all 0.15s ease;
    white-space: nowrap;
    user-select: none;
  }
  .lib-subtab:hover {
    color: var(--text);
    background: rgba(255, 255, 255, 0.06);
  }
  .lib-subtab.active {
    background: rgba(255, 255, 255, 0.12);
    color: var(--text);
    font-weight: 600;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-top: 22px;
    margin-bottom: 12px;
  }
  .section-title {
    font-size: 0.88rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .resume-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
  }
  .resume-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    display: flex;
    gap: 12px;
    align-items: center;
    transition: border-color 0.15s;
  }
  .resume-card:hover { border-color: var(--border-hover); }
  .resume-card img {
    width: 52px;
    height: 78px;
    object-fit: cover;
    border-radius: 4px;
    background: var(--surface-2);
    flex-shrink: 0;
  }

  .posters-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(156px, 1fr));
    gap: 14px;
    margin-top: 14px;
  }
  .poster-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    cursor: pointer;
    transition: border-color 0.15s;
    display: flex;
    flex-direction: column;
    position: relative;
  }
  .poster-card:hover { border-color: var(--border-hover); }
  .poster-card img {
    width: 100%;
    aspect-ratio: 2/3;
    object-fit: cover;
    background: var(--surface-2);
    display: block;
    filter: saturate(0.92);
  }
  .wl-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 28px;
    height: 28px;
    border-radius: 5px;
    background: rgba(9, 9, 11, 0.82);
    border: 1px solid var(--border-hover);
    color: var(--text);
    font-size: 0.85rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    padding: 0;
    z-index: 3;
  }
  .wl-btn:hover, .wl-btn.in-list {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
  }
  .poster-info {
    padding: 10px;
    display: flex;
    flex-direction: column;
    flex: 1;
    gap: 4px;
  }
  .poster-title {
    font-weight: 500;
    font-size: 0.85rem;
    line-height: 1.3;
    color: var(--text);
  }
  .poster-year {
    color: var(--dim);
    font-size: 0.75rem;
    margin-bottom: 6px;
  }
  .btn-oneclick {
    margin-top: auto;
    width: 100%;
    padding: 7px 10px;
    font-size: 0.77rem;
    font-weight: 500;
    border-radius: 5px;
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border);
    cursor: pointer;
    text-align: center;
  }
  .btn-oneclick:hover {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
  }

  /* ==========================================================================
     Unified Media Cards (Anime, Tendances Communauté, Custom Letterboxd Lists)
     ========================================================================== */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    cursor: pointer;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
    display: flex;
    flex-direction: column;
    position: relative;
    user-select: none;
  }
  .card:hover {
    border-color: var(--border-hover);
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }
  .card-poster-wrap,
  .card-thumb {
    position: relative;
    width: 100%;
    aspect-ratio: 2/3;
    background: var(--surface-2);
    overflow: hidden;
    display: block;
  }
  .card-poster-wrap img,
  .card-thumb img,
  .card-img {
    width: 100%;
    height: 100%;
    aspect-ratio: 2/3;
    object-fit: cover;
    display: block;
    filter: saturate(0.95);
    transition: transform 0.25s ease;
  }
  .card:hover .card-poster-wrap img,
  .card:hover .card-thumb img,
  .card:hover .card-img {
    transform: scale(1.04);
  }
  .card-badge {
    position: absolute;
    top: 8px;
    left: 8px;
    padding: 3px 7px;
    font-size: 0.72rem;
    font-weight: 700;
    border-radius: 4px;
    background: rgba(9, 9, 11, 0.85);
    backdrop-filter: blur(6px);
    color: #eab308;
    border: 1px solid rgba(255, 255, 255, 0.12);
    z-index: 2;
    display: inline-flex;
    align-items: center;
    line-height: 1.2;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
  }
  .card-badge-right {
    position: absolute;
    top: 8px;
    right: 8px;
    padding: 3px 7px;
    font-size: 0.7rem;
    font-weight: 600;
    border-radius: 4px;
    background: rgba(9, 9, 11, 0.85);
    backdrop-filter: blur(6px);
    color: #fafafa;
    border: 1px solid rgba(255, 255, 255, 0.12);
    z-index: 2;
    display: inline-flex;
    align-items: center;
    line-height: 1.2;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
  }
  .card-rating {
    position: absolute;
    top: 8px;
    left: 8px;
    padding: 3px 6px;
    font-size: 0.72rem;
    font-weight: 700;
    border-radius: 4px;
    background: rgba(9, 9, 11, 0.85);
    backdrop-filter: blur(6px);
    color: #eab308;
    border: 1px solid rgba(255, 255, 255, 0.12);
    z-index: 2;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
  }
  .card-actions {
    position: absolute;
    top: 8px;
    right: 8px;
    display: flex;
    gap: 5px;
    z-index: 3;
  }
  .card-action-btn {
    width: 26px;
    height: 26px;
    border-radius: 5px;
    background: rgba(9, 9, 11, 0.85);
    backdrop-filter: blur(4px);
    border: 1px solid var(--border-hover);
    color: var(--text);
    font-size: 0.8rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    padding: 0;
    transition: all 0.15s;
  }
  .card-action-btn:hover,
  .card-action-btn.active {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
  }
  .card-play-btn {
    position: absolute;
    bottom: 8px;
    left: 8px;
    right: 8px;
    padding: 6px;
    font-size: 0.76rem;
    font-weight: 600;
    border-radius: 5px;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(6px);
    color: #fafafa;
    border: 1px solid rgba(255, 255, 255, 0.2);
    cursor: pointer;
    text-align: center;
    opacity: 0;
    transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
    z-index: 3;
  }
  .card-play-btn:hover {
    background: #fafafa;
    color: #09090b;
  }
  .card:hover .card-play-btn {
    opacity: 1;
  }
  .card-wl-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 28px;
    height: 28px;
    border-radius: 5px;
    background: rgba(9, 9, 11, 0.85);
    backdrop-filter: blur(6px);
    border: 1px solid var(--border-hover);
    color: var(--text);
    font-size: 0.85rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    padding: 0;
    z-index: 3;
    transition: all 0.15s ease;
  }
  .card-wl-btn:hover,
  .card-wl-btn.in-list {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
  }
  .card-info {
    padding: 10px;
    display: flex;
    flex-direction: column;
    flex: 1;
    gap: 4px;
  }
  .card-title {
    font-weight: 600;
    font-size: 0.85rem;
    line-height: 1.3;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: var(--dim);
    font-size: 0.74rem;
  }
  .card-sub {
    color: var(--dim);
    font-size: 0.74rem;
    padding: 0 10px 10px 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .panel {
    margin-top: 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
  }
  .panel h3 {
    font-size: 0.96rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  .detail-layout {
    display: flex;
    gap: 22px;
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .detail-poster {
    width: 165px;
    aspect-ratio: 2/3;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    flex-shrink: 0;
  }
  .detail-body {
    flex: 1;
    min-width: 260px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .detail-title {
    font-size: 1.35rem;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .detail-sub {
    color: var(--muted);
    font-size: 0.82rem;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .detail-desc {
    color: #d4d4d8;
    font-size: 0.88rem;
    line-height: 1.55;
    max-width: 780px;
  }
  .detail-credits {
    color: var(--dim);
    font-size: 0.8rem;
    line-height: 1.45;
  }

  .episodes-grid {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
    max-height: 420px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .ep-card {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 10px 12px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    transition: border-color 0.15s;
  }
  .ep-card:hover, .ep-card.active { border-color: var(--border-hover); }
  .ep-thumb {
    width: 120px;
    height: 68px;
    object-fit: cover;
    border-radius: 4px;
    background: var(--surface-2);
    flex-shrink: 0;
    filter: saturate(0.9);
  }
  .ep-info { flex: 1; min-width: 180px; }
  .ep-title { font-size: 0.86rem; font-weight: 500; color: var(--text); }
  .ep-desc {
    font-size: 0.77rem;
    color: var(--dim);
    margin-top: 3px;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .filters {
    display: flex;
    gap: 6px;
    margin: 14px 0;
    flex-wrap: wrap;
    align-items: center;
  }
  .chip {
    padding: 5px 12px;
    border-radius: 5px;
    font-size: 0.8rem;
    cursor: pointer;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--muted);
    transition: 0.15s;
  }
  .chip:hover { border-color: var(--border-hover); color: var(--text); }
  .chip.active {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
    font-weight: 500;
  }
  .torrent-list { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
  .torrent-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    flex-wrap: wrap;
    transition: border-color 0.15s;
  }
  .torrent-item:hover { border-color: var(--border-hover); }
  .torrent-title {
    font-weight: 500;
    font-size: 0.85rem;
    word-break: break-word;
    line-height: 1.4;
    color: var(--text);
  }
  .torrent-meta {
    color: var(--dim);
    font-size: 0.77rem;
    margin-top: 4px;
  }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 500;
    background: var(--surface-2);
    color: var(--muted);
    border: 1px solid var(--border);
    margin-right: 5px;
    vertical-align: middle;
  }
  .badge-hi {
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
    font-weight: 600;
  }
  .modal-bg {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.8);
    display: none;
    justify-content: center;
    align-items: center;
    z-index: 100;
  }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 22px;
    width: 92%;
    max-width: 480px;
  }
  .progress-bar {
    height: 4px;
    background: var(--surface-2);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }
  .progress-fill {
    height: 100%;
    background: var(--text);
    transition: width 0.3s;
  }

  /* Lecteur Intégré KINO (Style Apple TV Monochrome) */
  .inapp-player-overlay {
    position: fixed;
    inset: 0;
    background: #000;
    z-index: 10000;
    display: none;
    flex-direction: column;
    justify-content: space-between;
    user-select: none;
  }
  .inapp-player-overlay.active {
    display: flex;
  }
  .inapp-player-overlay video {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
  }
  .inapp-hud-top {
    position: absolute;
    top: 0; left: 0; right: 0;
    padding: 20px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: linear-gradient(to bottom, rgba(9,9,11,0.9), transparent);
    z-index: 2;
    opacity: 1;
    transition: opacity 0.25s ease;
  }
  .inapp-hud-bottom {
    position: absolute;
    bottom: 0; left: 0; right: 0;
    padding: 24px 36px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: linear-gradient(to top, rgba(9,9,11,0.92), transparent);
    z-index: 2;
    opacity: 1;
    transition: opacity 0.25s ease;
  }
  .inapp-player-overlay.idle .inapp-hud-top,
  .inapp-player-overlay.idle .inapp-hud-bottom {
    opacity: 0;
    pointer-events: none;
  }
  .inapp-player-overlay.idle {
    cursor: none;
  }
  .inapp-seekbar-container {
    position: relative;
    width: 100%;
    height: 20px;
    display: flex;
    align-items: center;
    cursor: pointer;
  }
  .inapp-seekbar-bg {
    width: 100%;
    height: 3px;
    background: rgba(250,250,250,0.2);
    border-radius: 2px;
    position: relative;
    transition: height 0.15s ease;
  }
  .inapp-seekbar-container:hover .inapp-seekbar-bg {
    height: 5px;
  }
  .inapp-seekbar-fill {
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 0%;
    background: #fafafa;
    border-radius: 2px;
  }
  .inapp-controls-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #fafafa;
    font-size: 0.88rem;
  }
  .inapp-controls-group {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .inapp-btn {
    background: transparent;
    border: none;
    color: #fafafa;
    cursor: pointer;
    padding: 6px 10px;
    border-radius: 5px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.86rem;
    font-weight: 500;
    transition: background 0.15s, opacity 0.15s;
  }
  .inapp-btn-icon {
    width: 34px;
    height: 34px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
  }
  .inapp-vol-group {
    display: flex;
    align-items: center;
    gap: 8px;
    background: rgba(250, 250, 250, 0.06);
    padding: 2px 10px 2px 4px;
    border-radius: 20px;
    border: 1px solid rgba(250, 250, 250, 0.12);
    user-select: none;
  }
  .inapp-vol-slider-wrap {
    width: 84px;
    height: 22px;
    display: flex;
    align-items: center;
    cursor: pointer;
  }
  .inapp-vol-slider-bg {
    width: 100%;
    height: 4px;
    background: rgba(250, 250, 250, 0.22);
    border-radius: 2px;
    position: relative;
    transition: height 0.12s ease;
  }
  .inapp-vol-slider-wrap:hover .inapp-vol-slider-bg {
    height: 6px;
  }
  .inapp-vol-slider-fill {
    position: absolute;
    top: 0; left: 0; bottom: 0;
    background: #fafafa;
    border-radius: 2px;
    pointer-events: none;
  }
  .inapp-vol-text {
    font-size: 0.74rem;
    color: var(--muted);
    min-width: 32px;
    font-variant-numeric: tabular-nums;
  }
  .svg-icon {
    display: block;
    flex-shrink: 0;
  }
  .inapp-toast {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(17, 17, 19, 0.94);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 24px;
    color: var(--text);
    font-size: 0.88rem;
    display: none;
    z-index: 10;
    box-shadow: 0 10px 30px rgba(0,0,0,0.6);
    pointer-events: auto;
    text-align: center;
    max-width: 440px;
    line-height: 1.4;
  }
  .pywebview-drag-region {
    -webkit-app-region: drag;
  }
  .no-drag, .btn, .nav-tab, .chip, input, select, textarea, button, a {
    -webkit-app-region: no-drag;
  }
  /* En-tête gauche et boutons macOS (Traffic Lights) */
  .header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .mac-controls {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-right: 4px;
    -webkit-app-region: no-drag;
    user-select: none;
  }
  .mac-btn {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 1px solid rgba(0, 0, 0, 0.22);
    padding: 0;
    margin: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    outline: none;
    box-shadow: 0 0.5px 1.5px rgba(0, 0, 0, 0.35);
    transition: transform 0.08s ease, filter 0.12s ease;
  }
  .mac-btn:hover {
    filter: brightness(0.92);
  }
  .mac-btn:active {
    transform: scale(0.9);
    filter: brightness(0.8);
  }
  .mac-close {
    background: #ff5f56;
  }
  .mac-min {
    background: #ffbd2e;
  }
  .mac-max {
    background: #27c93f;
  }
  .mac-icon {
    opacity: 0;
    transition: opacity 0.12s ease-in-out;
    display: block;
    pointer-events: none;
  }
  .mac-controls:hover .mac-icon {
    opacity: 1;
  }

  /* Window resize handles for frameless native app */
  .win-resize-handles {
    pointer-events: none;
  }
  .win-resize-handle {
    position: fixed;
    z-index: 9999999;
    background: transparent;
    pointer-events: auto;
    -webkit-app-region: no-drag;
    user-select: none;
  }
  .rh-top { top: 0; left: 12px; right: 12px; height: 6px; cursor: n-resize; }
  .rh-bottom { bottom: 0; left: 12px; right: 12px; height: 6px; cursor: s-resize; }
  .rh-left { top: 12px; bottom: 12px; left: 0; width: 6px; cursor: w-resize; }
  .rh-right { top: 12px; bottom: 12px; right: 0; width: 6px; cursor: e-resize; }
  .rh-topleft { top: 0; left: 0; width: 14px; height: 14px; cursor: nwse-resize; z-index: 10000000; }
  .rh-topright { top: 0; right: 0; width: 14px; height: 14px; cursor: nesw-resize; z-index: 10000000; }
  .rh-bottomleft { bottom: 0; left: 0; width: 14px; height: 14px; cursor: nesw-resize; z-index: 10000000; }
  .rh-bottomright { bottom: 0; right: 0; width: 14px; height: 14px; cursor: nwse-resize; z-index: 10000000; }

  body.is-maximized .win-resize-handles,
  body.is-fullscreen .win-resize-handles,
  body.is-mac-native .win-resize-handles,
  body.is-mac-native .mac-controls,
  body.is-mac-native .win-controls {
    display: none !important;
  }
  body.is-mac-native header {
    padding-left: 96px;
    min-height: 52px;
  }
  body.is-mac-native .inapp-hud-top {
    padding-left: 96px;
  }

  /* Hero Spotlight Banner */
  .hero-spotlight {
    position: relative;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid var(--border);
    background: #09090b;
    min-height: 280px;
    margin-bottom: 20px;
    display: flex;
    align-items: flex-end;
    padding: 24px 26px;
    background-size: cover;
    background-position: center 22%;
    transition: background-image 0.35s ease;
  }
  .hero-spotlight::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, rgba(9,9,11,0.96) 0%, rgba(9,9,11,0.82) 46%, rgba(9,9,11,0.28) 100%),
                linear-gradient(0deg, rgba(9,9,11,0.95) 0%, rgba(9,9,11,0.2) 60%, rgba(9,9,11,0.5) 100%);
    pointer-events: none;
  }
  .hero-content {
    position: relative;
    z-index: 2;
    max-width: 620px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .hero-kicker {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--muted);
    font-weight: 600;
  }
  .hero-title {
    font-size: 1.75rem;
    font-weight: 700;
    color: #fafafa;
    letter-spacing: -0.02em;
    line-height: 1.15;
  }
  .hero-meta {
    font-size: 0.8rem;
    color: var(--muted);
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .hero-desc {
    font-size: 0.84rem;
    color: #d4d4d8;
    line-height: 1.48;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .hero-dots {
    position: absolute;
    right: 22px;
    bottom: 22px;
    z-index: 3;
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .hero-dot {
    width: 22px;
    height: 4px;
    border-radius: 2px;
    background: rgba(255,255,255,0.22);
    cursor: pointer;
    border: none;
    padding: 0;
    transition: background 0.2s, width 0.2s;
  }
  .hero-dot.active {
    background: #fafafa;
    width: 32px;
  }

  /* Drag & Drop .torrent / Magnet Overlay */
  .drop-zone-overlay {
    position: fixed;
    inset: 0;
    z-index: 999990;
    background: rgba(9, 9, 11, 0.90);
    backdrop-filter: blur(8px);
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
  }
  .drop-zone-overlay.active {
    display: flex;
  }
  .drop-zone-box {
    border: 2px dashed #fafafa;
    border-radius: 14px;
    padding: 42px 56px;
    text-align: center;
    background: rgba(18, 18, 21, 0.92);
    max-width: 500px;
  }

  /* Lecteur intégré : Skip Intro & Next Episode Card */
  .inapp-floating-card {
    position: absolute;
    right: 28px;
    bottom: 92px;
    z-index: 15;
    background: rgba(18, 18, 21, 0.94);
    border: 1px solid rgba(250, 250, 250, 0.35);
    border-radius: 8px;
    padding: 12px 16px;
    color: #fafafa;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(8px);
    display: none;
    align-items: center;
    gap: 12px;
  }

  /* Roulette KINO (Mode Surprends-moi — DA monochrome épurée) */
  .btn-surprise {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 12px;
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border-hover);
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .btn-surprise:hover {
    background: var(--surface-2);
    border-color: var(--text);
  }
  .case-modal-box {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 22px 22px;
    width: 95%;
    max-width: 940px;
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.85);
    position: relative;
    overflow: hidden;
  }
  .case-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
    gap: 10px;
    flex-wrap: wrap;
  }
  .case-title-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .case-kicker {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    background: var(--bg);
    border: 1px solid var(--border);
    padding: 3px 8px;
    border-radius: 4px;
  }
  .case-roller-viewport {
    position: relative;
    width: 100%;
    height: 244px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    user-select: none;
  }
  /* Vignettes sombres gauche / droite */
  .case-roller-viewport::before,
  .case-roller-viewport::after {
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    width: 120px;
    z-index: 5;
    pointer-events: none;
  }
  .case-roller-viewport::before {
    left: 0;
    background: linear-gradient(90deg, var(--bg) 0%, rgba(9, 9, 11, 0.82) 45%, transparent 100%);
  }
  .case-roller-viewport::after {
    right: 0;
    background: linear-gradient(270deg, var(--bg) 0%, rgba(9, 9, 11, 0.82) 45%, transparent 100%);
  }
  /* Repère central blanc sobre */
  .case-center-laser {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 2px;
    transform: translateX(-50%);
    background: #fafafa;
    z-index: 8;
    pointer-events: none;
  }
  .case-center-laser::before,
  .case-center-laser::after {
    content: "";
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    width: 0;
    height: 0;
    border-left: 7px solid transparent;
    border-right: 7px solid transparent;
  }
  .case-center-laser::before {
    top: 0;
    border-top: 9px solid #fafafa;
  }
  .case-center-laser::after {
    bottom: 0;
    border-bottom: 9px solid #fafafa;
  }
  .case-roller-strip {
    display: flex;
    align-items: center;
    height: 100%;
    gap: 10px;
    padding: 0 12px;
    will-change: transform;
  }
  .case-slot-card {
    flex: 0 0 152px;
    width: 152px;
    height: 220px;
    background: var(--surface);
    border-radius: 6px;
    overflow: hidden;
    position: relative;
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    transition: transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1), border-color 0.25s ease;
  }
  .case-slot-card.winner-locked {
    transform: scale(1.04);
    border-color: #fafafa;
    z-index: 4;
  }
  .case-slot-poster {
    width: 100%;
    height: 170px;
    object-fit: cover;
    background: var(--surface-2);
    display: block;
  }
  .case-slot-rating {
    position: absolute;
    top: 7px;
    right: 7px;
    z-index: 2;
    font-size: 0.67rem;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    background: rgba(9, 9, 11, 0.9);
    color: #fafafa;
    border: 1px solid var(--border-hover);
  }
  .case-slot-info {
    position: relative;
    z-index: 2;
    padding: 6px 8px;
    background: var(--surface);
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .case-slot-title {
    font-size: 0.73rem;
    font-weight: 600;
    color: #fafafa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .case-slot-sub {
    font-size: 0.66rem;
    color: var(--muted);
    margin-top: 2px;
  }
  .case-winner-panel {
    margin-top: 14px;
    padding: 15px 18px;
    border-radius: 8px;
    background: var(--bg);
    border: 1px solid var(--border-hover);
    display: none;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    animation: caseWinnerIn 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes caseWinnerIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
</style>
</head>
<body>

<!-- Overlay Glisser-Déposer (.torrent / Magnet) -->
<div id="dropZoneOverlay" class="drop-zone-overlay">
  <div class="drop-zone-box">
    <div style="margin-bottom:12px;">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="color:var(--text); opacity:0.85;">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
    </div>
    <div style="font-size:1.1rem; font-weight:600; color:#fafafa;">Déposez votre fichier .torrent ou lien Magnet</div>
    <div style="font-size:0.82rem; color:var(--muted); margin-top:6px;">Débridage et lecture instantanée via votre compte Cloud</div>
  </div>
</div>

<div id="windowResizeHandles" class="win-resize-handles">
  <div class="win-resize-handle rh-top" data-dir="top"></div>
  <div class="win-resize-handle rh-bottom" data-dir="bottom"></div>
  <div class="win-resize-handle rh-left" data-dir="left"></div>
  <div class="win-resize-handle rh-right" data-dir="right"></div>
  <div class="win-resize-handle rh-topleft" data-dir="topleft"></div>
  <div class="win-resize-handle rh-topright" data-dir="topright"></div>
  <div class="win-resize-handle rh-bottomleft" data-dir="bottomleft"></div>
  <div class="win-resize-handle rh-bottomright" data-dir="bottomright"></div>
</div>

<header class="pywebview-drag-region" ondblclick="windowAction('maximize')">
  <div class="header-left">
    <div class="mac-controls no-drag">
      <button class="mac-btn mac-close" onclick="windowAction('close')" title="Fermer (Alt+F4)">
        <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
          <path d="M2.2 2.2l7.6 7.6M9.8 2.2l-7.6 7.6" stroke="rgba(0,0,0,0.65)" stroke-width="1.3" stroke-linecap="round"/>
        </svg>
      </button>
      <button class="mac-btn mac-min" onclick="windowAction('minimize')" title="Réduire (Ctrl+M)">
        <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
          <line x1="2" y1="6" x2="10" y2="6" stroke="rgba(0,0,0,0.65)" stroke-width="1.3" stroke-linecap="round"/>
        </svg>
      </button>
      <button class="mac-btn mac-max" onclick="windowAction('maximize')" title="Agrandir / Plein écran (F11)">
        <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
          <polygon points="2.2,7.5 2.2,2.2 7.5,2.2" fill="rgba(0,0,0,0.65)"/>
          <polygon points="9.8,4.5 9.8,9.8 4.5,9.8" fill="rgba(0,0,0,0.65)"/>
        </svg>
      </button>
    </div>
    <div class="logo no-drag" onclick="switchTab('movies')">
      <svg class="logo-mark" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="2" y="2" width="28" height="28" rx="3" stroke="#fafafa" stroke-width="2"/>
        <line x1="10.5" y1="7.5" x2="10.5" y2="24.5" stroke="#fafafa" stroke-width="2.4"/>
        <polygon points="12,16 23.5,7.5 23.5,24.5" fill="#fafafa"/>
      </svg>
      <span class="logo-wordmark">KINO</span>
      <span class="logo-divider"></span>
      <span class="logo-sub" id="logoProviderSub">Real-Debrid</span>
    </div>
  </div>
  <div class="header-right no-drag">
    <!-- Badge Utilisateur interactif / statut -->
    <div id="userBadge" class="header-user-pill" onclick="openConfig()" title="Gérer le compte et la configuration">
      <span class="user-status-dot"></span>
      <span style="color:var(--muted); font-size:0.75rem;">Chargement...</span>
    </div>

    <div class="header-v-divider"></div>

    <!-- Bouton rapide + Torrent -->
    <input type="file" id="torrentFileInput" accept=".torrent" style="display:none;" onchange="handleTorrentFileSelect(this.files)">
    <button class="header-tool-btn header-btn-torrent" onclick="document.getElementById('torrentFileInput').click()" title="Ouvrir un fichier .torrent (ou glisser-déposer)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      <span>Torrent</span>
    </button>

    <!-- Groupe de fonctionnalités sociales & extensions (icônes vectorielles épurées) -->
    <div class="header-action-group">
      <button class="header-tool-btn" onclick="openRemoteModal()" title="Télécommande smartphone sans fil via QR Code">
        <svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
        <span class="btn-text">Remote</span>
      </button>
      <button class="header-tool-btn" onclick="openWatchPartyModal()" title="Watch Party synchronisée P2P (Code à 6 lettres)">
        <svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span class="btn-text">Watch Party</span>
      </button>
      <button class="header-tool-btn" onclick="openAddonsModal()" title="Add-ons et Scrapers Communautaires">
        <svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>
        <span class="btn-text">Add-ons</span>
      </button>
    </div>

    <!-- Menu Déroulant Réglages & Outils -->
    <div class="header-menu-wrap">
      <button id="headerSettingsBtn" class="header-tool-btn header-btn-settings" onclick="toggleHeaderMenu(event)" title="Réglages et Outils">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
        <span class="btn-text">Réglages</span>
        <svg class="chevron-icon" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
      </button>

      <div id="headerDropdownMenu" class="header-dropdown-menu">
        <div class="header-dropdown-item" onclick="openConfig(); closeHeaderMenu();">
          <span class="hdi-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </span>
          <div class="hdi-content">
            <span class="hdi-title">Configuration</span>
            <span class="hdi-desc">Débrideurs, Trakt.tv, Qualité vidéo</span>
          </div>
        </div>
        <div class="header-dropdown-item" onclick="openFolder(); closeHeaderMenu();">
          <span class="hdi-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          </span>
          <div class="hdi-content">
            <span class="hdi-title">Dossier Téléchargements</span>
            <span class="hdi-desc">Ouvrir l'emplacement local des vidéos</span>
          </div>
        </div>
        <div class="header-dropdown-item" onclick="triggerGdriveSync(); closeHeaderMenu();">
          <span class="hdi-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>
          </span>
          <div class="hdi-content">
            <span class="hdi-title">Synchronisation Cloud</span>
            <span class="hdi-desc">Synchroniser Google Drive (Mac ⇄ PC)</span>
          </div>
        </div>
        <div class="header-dropdown-divider"></div>
        <div class="header-dropdown-item" onclick="toggleShortcutsModal(); closeHeaderMenu();">
          <span class="hdi-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2" ry="2"/><line x1="6" y1="8" x2="6.01" y2="8"/><line x1="10" y1="8" x2="10.01" y2="8"/><line x1="14" y1="8" x2="14.01" y2="8"/><line x1="18" y1="8" x2="18.01" y2="8"/><line x1="6" y1="12" x2="6.01" y2="12"/><line x1="18" y1="12" x2="18.01" y2="12"/><line x1="7" y1="16" x2="17" y2="16"/></svg>
          </span>
          <div class="hdi-content">
            <span class="hdi-title">Raccourcis Clavier &amp; Aide</span>
            <span class="hdi-desc">Commandes du lecteur et raccourcis</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</header>

<div class="container">
  <!-- Barre de recherche -->
  <div class="search-box" style="position:relative;">
    <select id="searchType" onchange="toggleSearchMode()">
      <option value="movie">Film</option>
      <option value="series">Série</option>
      <option value="anime">Anime</option>
      <option value="raw">Mots-clés</option>
      <option value="magnet">Magnet</option>
    </select>
    <div style="flex:1; position:relative; display:flex;">
      <input type="text" id="searchInput" placeholder="Rechercher un film, une série ou un anime... (⌘K)" oninput="onSearchInput()" onkeydown="onSearchKeyDown(event)" autocomplete="off" style="width:100%;">
      <div id="searchDropdown" class="search-dropdown" style="display:none;"></div>
    </div>
    <button class="btn" onclick="runSearch()">Rechercher</button>
  </div>

  <!-- Navigation principale épurée (5 onglets primaires sans encombrement) -->
  <div class="nav-tabs">
    <div class="nav-tabs-primary">
      <button class="nav-tab active" id="tab-movies" onclick="switchTab('movies')">Films</button>
      <button class="nav-tab" id="tab-series" onclick="switchTab('series')">Séries</button>
      <button class="nav-tab" id="tab-anime" onclick="switchTab('anime')">Anime</button>
      <button class="nav-tab" id="tab-community" onclick="switchTab('community')">Tendances</button>
      <button class="nav-tab" id="tab-library" onclick="switchTab('library')">
        <span>Ma Bibliothèque</span>
        <span id="libraryTotalBadge" class="nav-badge"></span>
      </button>
    </div>
  </div>

  <!-- Sous-navigation Bibliothèque (contextuelle, propre et fluide) -->
  <div id="librarySubNav" class="library-subnav" style="display:none;">
    <button class="lib-subtab active" id="subtab-watchlist" onclick="switchTab('watchlist')">Ma Liste <span id="wlCount"></span></button>
    <button class="lib-subtab" id="subtab-history" onclick="switchTab('history')">Reprendre <span id="histCount"></span></button>
    <button class="lib-subtab" id="subtab-watched" onclick="switchTab('watched')">Déjà vus <span id="watchedCount"></span></button>
    <button class="lib-subtab" id="subtab-customlists" onclick="switchTab('customlists')">Listes Letterboxd <span id="customListsCount"></span></button>
    <button class="lib-subtab" id="subtab-rdcloud" onclick="switchTab('rdcloud')">Cloud RD</button>
  </div>

  <!-- Téléchargements PC actifs -->
  <div id="downloadsPanel" class="panel" style="display:none;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
      <h3>Téléchargements</h3>
      <button class="btn btn-secondary" style="padding:5px 10px; font-size:0.78rem;" onclick="openFolder()">Ouvrir le dossier</button>
    </div>
    <div id="downloadsList"></div>
  </div>

  <!-- Résultat Débridage -->
  <div id="debridResultPanel" class="panel" style="display:none;"></div>

  <!-- Fiche Détail Média & Grille d'épisodes -->
  <div id="detailPanel" class="panel" style="display:none;"></div>

  <!-- Panneau des torrents -->
  <div id="torrentsPanel" class="panel" style="display:none;">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
      <div>
        <h3 id="torrentsHeading">Sources</h3>
        <p id="torrentsSub" style="color:var(--dim); font-size:0.8rem; margin-top:3px;"></p>
      </div>
      <div id="seriesControls" style="display:none; gap:8px; align-items:center; flex-wrap:wrap;">
        <label style="font-size:0.8rem; color:var(--muted);">Saison</label>
        <select id="seasonSelect" onchange="onSeasonChange()" style="min-width:95px;"></select>
        <button class="btn btn-secondary" id="markSeasonWatchedBtn" onclick="toggleWatchedWholeSeason()" title="Marquer ou démarquer tous les épisodes de cette saison comme vus" style="padding:6px 10px; font-size:0.76rem;">✓ Saison vue</button>
        <label style="font-size:0.8rem; color:var(--muted);">Épisode</label>
        <select id="episodeSelect" onchange="reloadSeriesEpisode()" style="min-width:190px;"></select>
        <button class="btn btn-secondary" onclick="reloadSeriesEpisode()">Sources</button>
        <button class="btn" onclick="oneClickSeriesEpisode(this)">Play</button>
      </div>
    </div>

    <div class="filters" style="display:flex; align-items:center; flex-wrap:wrap; gap:8px;">
      <span class="chip active" onclick="setFilter('', this)">Tous</span>
      <span class="chip" onclick="setFilter('fr', this)">VF / MULTI / VOSTFR</span>
      <span class="chip" onclick="setFilter('sdr', this)" title="Afficher uniquement les sources SDR">SDR</span>
      <span class="chip" onclick="setFilter('4k', this)">4K</span>
      <span class="chip" onclick="setFilter('1080p', this)">1080p</span>
      <div style="margin-left:auto; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <select id="torrentSortSelect" onchange="renderTorrents()" style="padding:5px 9px; font-size:0.78rem; width:auto; border-radius:6px; background:var(--surface); border:1px solid var(--border); color:var(--text); cursor:pointer;">
          <option value="default">Tri : Recommandé</option>
          <option value="seeds">Tri : Seeders (Max)</option>
          <option value="size_desc">Tri : Taille (Plus grand)</option>
          <option value="size_asc">Tri : Taille (Plus léger)</option>
        </select>
        <input type="text" id="customFilter" placeholder="Filtrer (ex: multi, hevc...)" oninput="renderTorrents()" style="padding:5px 10px; font-size:0.78rem; min-width:180px;">
      </div>
    </div>

    <div id="torrentsList" class="torrent-list"></div>
  </div>

  <!-- Bandeau Reprendre la lecture (affiché sur l'accueil) -->
  <div id="homeResumeSection" style="display:none;">
    <div class="section-header">
      <span class="section-title">Reprendre la lecture</span>
      <button class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem;" onclick="clearAllHistory()">Effacer</button>
    </div>
    <div id="homeResumeGrid" class="resume-grid"></div>
  </div>

  <!-- Bannière Hero Spotlight Cinéma -->
  <div id="heroSpotlight" class="hero-spotlight" style="display:none;"></div>

  <!-- Section Catalogue / Grille principale -->
  <div id="catalogHeader" class="section-header" style="flex-direction:column; align-items:stretch; gap:10px;">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span id="catalogTitle" class="section-title">Films populaires du moment</span>
        <span id="listStatsBadge" style="display:none; font-size:0.73rem; font-weight:500; color:var(--muted); background:var(--surface); border:1px solid var(--border); padding:3px 9px; border-radius:5px;"></span>
      </div>
      <div id="catalogSortWrap" style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn-surprise" onclick="surpriseMeMedia(activeTab === 'classics' ? 'classics' : 'catalog')" title="Lancer la roulette KINO et tirer un titre non encore vu">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="4" width="20" height="16" rx="3"/>
            <line x1="12" y1="4" x2="12" y2="20"/>
          </svg>
          <span>Surprends-moi</span>
        </button>
        <button id="hideWatchedChip" type="button" class="chip" style="padding:4px 10px; font-size:0.75rem;" onclick="toggleHideWatchedCatalog()" title="Masquer du catalogue les films que vous avez déjà vus">Masquer déjà vus</button>
        <span style="font-size:0.76rem; color:var(--dim);">Tri :</span>
        <select id="catalogSortSelect" style="padding:4px 10px; font-size:0.78rem; width:auto;" onchange="onChangeCatalogSort(this.value)">
          <option value="top">Sélection / Tendances</option>
          <option value="imdbRating">★ Mieux notés IMDb</option>
          <option value="recent">Plus récents</option>
          <option value="oldest">Chronologique (Anciens)</option>
        </select>
      </div>
      <div id="watchlistActionsWrap" style="display:none; gap:8px; align-items:center; flex-wrap:wrap;">
        <input type="text" id="listFilterInput" placeholder="Filtrer par titre ou année..." oninput="renderActiveListTab()" style="padding:5px 10px; font-size:0.76rem; width:190px;">
        <select id="listSortSelect" style="padding:4px 10px; font-size:0.76rem; width:auto;" onchange="renderActiveListTab()">
          <option value="added">Tri : Ajout récent</option>
          <option value="rating">Tri : ★ Note IMDb</option>
          <option value="year_desc">Tri : Plus récents</option>
          <option value="year_asc">Tri : Plus anciens</option>
          <option value="alpha">Tri : A → Z</option>
        </select>
        <button id="listSurpriseBtn" class="btn-surprise" onclick="surpriseMeFromCurrentList()" title="Lancer la roulette parmi les films de cette liste">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="4" width="20" height="16" rx="3"/>
            <line x1="12" y1="4" x2="12" y2="20"/>
          </svg>
          <span id="listSurpriseBtnLabel">Tirage sur Ma Liste</span>
        </button>
        <button class="btn btn-secondary" style="display:inline-flex; align-items:center; gap:7px; padding:5px 12px; font-size:0.76rem; border-color:rgba(0,224,84,0.4);" onclick="openLetterboxdModal()">
          <svg width="20" height="10" viewBox="0 0 30 12" fill="none">
            <circle cx="6" cy="6" r="5" fill="#ff8000"/>
            <circle cx="15" cy="6" r="5" fill="#00e054"/>
            <circle cx="24" cy="6" r="5" fill="#40bcf4"/>
          </svg>
          <span>Importer Letterboxd</span>
        </button>
      </div>
    </div>
    <div id="genreFilters" style="display:flex; gap:5px; flex-wrap:wrap; align-items:center;">
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Tous</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Thriller" onclick="selectGenre('Thriller', this)">Thriller</span>
      <span class="chip" data-genre="Crime" onclick="selectGenre('Crime', this)">Policier</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Animation" onclick="selectGenre('Animation', this)">Animation</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Horror" onclick="selectGenre('Horror', this)">Horreur</span>
      <span class="chip" data-genre="War" onclick="selectGenre('War', this)">Guerre</span>
      <span class="chip" data-genre="Western" onclick="selectGenre('Western', this)">Western</span>
      <span class="chip" data-genre="Documentary" onclick="selectGenre('Documentary', this)">Documentaire</span>
    </div>
  </div>
  <div id="postersGrid" class="posters-grid"></div>
  <div id="loadMoreWrap" style="display:none; justify-content:center; margin-top:22px;">
    <button id="loadMoreBtn" class="btn btn-secondary" style="padding:9px 24px;" onclick="loadMoreCatalog()">Voir plus de titres</button>
  </div>

  <!-- Section Cloud Multi-Debrid -->
  <div id="rdCloudPanel" class="panel" style="display:none;">
    <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:14px;">
      <div>
        <h3 id="rdCloudHeading">Derniers fichiers débridés sur le Cloud</h3>
        <div id="rdCloudStatusNote" style="color:var(--dim); font-size:0.76rem; margin-top:3px;">Gérez ou nettoyez automatiquement les fichiers et torrents stockés sur votre débrideur.</div>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <select id="rdCloudRetentionSelect" style="padding:5px 10px; font-size:0.78rem; width:auto;" onchange="onChangeRdRetention(this.value)" title="Suppression automatique des fichiers et torrents anciens">
          <option value="0">Auto-suppression : Jamais</option>
          <option value="1">Auto-suppression : &gt; 24h</option>
          <option value="3">Auto-suppression : &gt; 3 jours</option>
          <option value="7">Auto-suppression : &gt; 7 jours</option>
          <option value="14">Auto-suppression : &gt; 14 jours</option>
          <option value="30">Auto-suppression : &gt; 30 jours</option>
        </select>
        <button class="btn btn-secondary" style="padding:5px 10px; font-size:0.78rem;" onclick="purgeRdCloud('all', this)">Tout vider</button>
        <button class="btn btn-secondary" style="padding:5px 10px; font-size:0.78rem;" onclick="loadRdCloud()">Actualiser</button>
      </div>
    </div>
    <div id="rdCloudList" class="torrent-list"></div>
  </div>

  <!-- Panneau Communauté & Tendances Letterboxd -->
  <div id="communityPanel" class="panel" style="display:none;">
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:16px;">
      <div>
        <h3 style="font-size:1.15rem; font-weight:700;">Tendances Cinéphiles &amp; Collections</h3>
        <p style="color:var(--dim); font-size:0.8rem; margin-top:3px;">Collections cultes, palmarès Letterboxd et pépites sélectionnées par la communauté.</p>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; width:100%; max-width:540px;">
        <input type="text" id="communityLetterboxdInput" placeholder="Coller une URL Letterboxd (ex: https://letterboxd.com/... ou boxd.it/...)" style="flex:1; padding:7px 12px; font-size:0.8rem; border-radius:6px; background:var(--surface); border:1px solid var(--border); color:var(--text);">
        <button class="btn" style="padding:7px 14px; font-size:0.8rem;" onclick="importCustomLetterboxdFromInput()">Importer</button>
      </div>
    </div>

    <!-- Grille des sélections communautaires -->
    <div id="communityListsGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(290px, 1fr)); gap:14px; margin-bottom:24px;"></div>

    <!-- Titres de la liste sélectionnée -->
    <div id="communityActiveListSection" style="display:none;">
      <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border); padding-bottom:10px; margin-bottom:14px;">
        <div>
          <h4 id="communityActiveListTitle" style="font-size:1rem; font-weight:700;"></h4>
          <p id="communityActiveListDesc" style="color:var(--muted); font-size:0.78rem; margin-top:3px;"></p>
        </div>
        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.76rem;" onclick="closeCommunityActiveList()">← Fermer la sélection</button>
      </div>
      <div id="communityActiveListGrid" class="posters-grid"></div>
    </div>
  </div>
</div>

<!-- Filtres SVG Gamma pour déboucher les ombres / scènes sombres sans brûler les blancs -->
<svg width="0" height="0" style="position:absolute; pointer-events:none;">
  <defs>
    <filter id="kinoShadowBoost1" color-interpolation-filters="sRGB">
      <feComponentTransfer>
        <feFuncR type="gamma" amplitude="1" exponent="0.76" offset="0.015"/>
        <feFuncG type="gamma" amplitude="1" exponent="0.76" offset="0.015"/>
        <feFuncB type="gamma" amplitude="1" exponent="0.76" offset="0.015"/>
      </feComponentTransfer>
    </filter>
    <filter id="kinoShadowBoost2" color-interpolation-filters="sRGB">
      <feComponentTransfer>
        <feFuncR type="gamma" amplitude="1" exponent="0.62" offset="0.025"/>
        <feFuncG type="gamma" amplitude="1" exponent="0.62" offset="0.025"/>
        <feFuncB type="gamma" amplitude="1" exponent="0.62" offset="0.025"/>
      </feComponentTransfer>
    </filter>
    <filter id="kinoShadowBoost3" color-interpolation-filters="sRGB">
      <feComponentTransfer>
        <feFuncR type="gamma" amplitude="1" exponent="0.50" offset="0.038"/>
        <feFuncG type="gamma" amplitude="1" exponent="0.50" offset="0.038"/>
        <feFuncB type="gamma" amplitude="1" exponent="0.50" offset="0.038"/>
      </feComponentTransfer>
    </filter>
  </defs>
</svg>

<!-- Lecteur Intégré KINO (Plein écran dans l'app) -->
<div id="inAppPlayerOverlay" class="inapp-player-overlay">
  <video id="inAppVideo" playsinline></video>
  <div id="inAppSubOverlay" style="position:absolute; left:50%; bottom:86px; transform:translateX(-50%); z-index:12; max-width:82%; text-align:center; pointer-events:none; font-size:1.32rem; font-weight:600; color:#fafafa; text-shadow:0 2px 6px rgba(0,0,0,0.95), 0 0 12px rgba(0,0,0,0.85); background:rgba(9,9,11,0.68); padding:5px 14px; border-radius:6px; display:none; line-height:1.38;"></div>
  <div id="inAppStatusToast" class="inapp-toast"></div>
  <!-- Bouton Skip Intro (+85s) -->
  <div id="inAppSkipIntroCard" class="inapp-floating-card">
    <button class="btn" style="padding:7px 14px; font-size:0.82rem;" onclick="skipInAppIntro()">⏭ Passer l'intro (+85s)</button>
    <button class="btn btn-secondary" style="padding:6px 9px; font-size:0.76rem;" onclick="dismissSkipIntro()" title="Masquer">✕</button>
  </div>
  <!-- Carte Prochain épisode dans 10s -->
  <div id="inAppNextEpCard" class="inapp-floating-card">
    <div>
      <div style="font-size:0.72rem; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em;">À suivre dans <span id="inAppNextEpCountdown">10</span>s</div>
      <div id="inAppNextEpTitle" style="font-size:0.86rem; font-weight:600; margin-top:2px; max-width:240px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Épisode suivant</div>
    </div>
    <button class="btn" style="padding:6px 12px; font-size:0.78rem;" onclick="inAppNextTrack()">▶ Lancer</button>
    <button class="btn btn-secondary" style="padding:6px 9px; font-size:0.76rem;" onclick="cancelNextEpAuto()">Annuler</button>
  </div>
  <div class="inapp-hud-top pywebview-drag-region" ondblclick="windowAction('maximize')">
    <div style="display:flex; align-items:center; gap:14px;" class="no-drag">
      <div class="mac-controls">
        <button class="mac-btn mac-close" onclick="windowAction('close')" title="Fermer (Alt+F4)">
          <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
            <path d="M2.2 2.2l7.6 7.6M9.8 2.2l-7.6 7.6" stroke="rgba(0,0,0,0.65)" stroke-width="1.3" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="mac-btn mac-min" onclick="windowAction('minimize')" title="Réduire (Ctrl+M)">
          <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
            <line x1="2" y1="6" x2="10" y2="6" stroke="rgba(0,0,0,0.65)" stroke-width="1.3" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="mac-btn mac-max" onclick="windowAction('maximize')" title="Agrandir / Plein écran (F11)">
          <svg class="mac-icon" viewBox="0 0 12 12" width="7" height="7">
            <polygon points="2.2,7.5 2.2,2.2 7.5,2.2" fill="rgba(0,0,0,0.65)"/>
            <polygon points="9.8,4.5 9.8,9.8 4.5,9.8" fill="rgba(0,0,0,0.65)"/>
          </svg>
        </button>
      </div>
      <button class="inapp-btn" onclick="closeInAppPlayer()">← Retour</button>
    </div>
    <div id="inAppTitle" style="font-weight:600; font-size:0.95rem; color:#fafafa; text-align:center; flex:1; margin:0 16px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"></div>
    <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;" class="no-drag">
      <button class="inapp-btn" id="inAppMpvSuggestBtn" onclick="switchToExternalPlayer()" style="display:none; border-color:rgba(124,58,237,0.8); background:rgba(124,58,237,0.18);" title="Optimisé pour 4K HDR & DTS sans saccades"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>Basculer MPV</button>
      <button class="inapp-btn" id="inAppAudioFixBtn" onclick="fixInAppAudio()" style="display:none; border-color:rgba(234,179,8,0.75); color:#facc15; background:rgba(234,179,8,0.14);" title="Convertir l'audio multi-canal (DTS/TrueHD/E-AC3) en flux stéréo AAC compatible avec le navigateur"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><line x1="1" y1="1" x2="23" y2="23"/></svg>Son AAC</button>
      <button class="inapp-btn" id="inAppSubsBtn" onclick="cycleInAppSubtitles()" title="Sous-titres OpenSubtitles FR / EN (Touche C)">CC : Off</button>
      <button class="inapp-btn" id="inAppSubSizeBtn" onclick="cycleInAppSubSize()" title="Taille des sous-titres (S / M / L / XL)">A±</button>
      <button class="inapp-btn" id="inAppAudioBtn" onclick="cycleInAppAudioBoost()" title="Boost des dialogues / Mode Audio Nuit (Touche V)">Voix : Normal</button>
      <button class="inapp-btn" id="inAppClarityBtn" onclick="cycleInAppClarity()" title="Déboucher les noirs / Éclaircir les scènes sombres (Touche B)">Clarté : Normal</button>
      <button class="inapp-btn" id="inAppSpeedBtn" onclick="cycleInAppSpeed()" title="Vitesse de lecture (Touches [ et ])">1.0x</button>
      <button class="inapp-btn" id="inAppShotBtn" onclick="captureInAppScreenshot()" title="Capturer une image du film dans Téléchargements"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></button>
      <button class="inapp-btn" id="inAppRemoteBtn" onclick="openRemoteModal()" title="Télécommande smartphone tactile via QR Code"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>Remote</button>
      <button class="inapp-btn" id="inAppWpBtn" onclick="openWatchPartyModal()" title="Watch Party synchronisée P2P (Code à 6 lettres)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>Watch Party</button>
      <button class="inapp-btn" id="inAppExternalBtn" onclick="switchToExternalPlayer()">Lecteur externe</button>
    </div>
  </div>
  <!-- Volet Chat Watch Party dans le lecteur -->
  <div id="inAppWpChatBox" style="position:absolute; right:20px; bottom:90px; width:280px; max-height:260px; background:rgba(18,18,21,0.92); backdrop-filter:blur(10px); border:1px solid var(--border); border-radius:12px; display:none; flex-direction:column; z-index:25; box-shadow:0 8px 30px rgba(0,0,0,0.8);">
    <div style="padding:10px 12px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center;">
      <span style="font-size:0.8rem; font-weight:600; color:#fafafa;">Watch Party Chat</span>
      <button class="btn btn-secondary" style="padding:2px 6px; font-size:0.7rem;" onclick="toggleWpChat()">✕</button>
    </div>
    <div id="inAppWpMessages" style="flex:1; overflow-y:auto; padding:10px; font-size:0.78rem; display:flex; flex-direction:column; gap:6px; max-height:160px;"></div>
    <div style="padding:8px 10px; border-top:1px solid var(--border); display:flex; gap:6px;">
      <input type="text" id="inAppWpInput" placeholder="Message..." onkeydown="if(event.key==='Enter') sendWpMessage()" style="flex:1; padding:4px 8px; font-size:0.78rem; border-radius:6px; background:var(--surface-2); border:1px solid var(--border); color:#fafafa;">
      <button class="btn" style="padding:4px 9px; font-size:0.75rem;" onclick="sendWpMessage()">Envoyer</button>
    </div>
  </div>
  <div class="inapp-hud-bottom">
    <div class="inapp-seekbar-container" id="inAppSeekbar" onclick="seekInApp(event)">
      <div class="inapp-seekbar-bg">
        <div id="inAppSeekFill" class="inapp-seekbar-fill"></div>
      </div>
    </div>
    <div class="inapp-controls-row">
      <div class="inapp-controls-group">
        <button class="inapp-btn inapp-btn-icon" onclick="inAppPrevTrack()" title="Épisode précédent (<)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
        </button>
        <button class="inapp-btn inapp-btn-icon" onclick="inAppSeekRel(-10)" title="Reculer 10s (Flèche Gauche)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>
            <text x="12" y="15" font-size="7" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">10</text>
          </svg>
        </button>
        <button class="inapp-btn inapp-btn-icon" id="inAppPlayPauseBtn" onclick="toggleInAppPlay()" title="Lecture / Pause (Espace)" style="min-width:36px;">
          <svg class="svg-icon" viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
        </button>
        <button class="inapp-btn inapp-btn-icon" onclick="inAppSeekRel(10)" title="Avancer 10s (Flèche Droite)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            <text x="12" y="15" font-size="7" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">10</text>
          </svg>
        </button>
        <button class="inapp-btn inapp-btn-icon" onclick="inAppNextTrack()" title="Épisode suivant (>)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
        </button>
        <span id="inAppTimeText" style="color:var(--muted); font-size:0.82rem; margin-left:8px; font-variant-numeric:tabular-nums;">00:00 / 00:00</span>
      </div>
      <div class="inapp-controls-group">
        <div class="inapp-vol-group" id="inAppVolGroup">
          <button class="inapp-btn inapp-btn-icon" id="inAppMuteBtn" onclick="toggleInAppMute()" title="Muet (M)">
            <span id="inAppVolIcon">
              <svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
              </svg>
            </span>
          </button>
          <div class="inapp-vol-slider-wrap" id="inAppVolSliderWrap" onmousedown="onVolMouseDown(event)" onclick="handleVolScrub(event)">
            <div class="inapp-vol-slider-bg">
              <div id="inAppVolFill" class="inapp-vol-slider-fill" style="width: 100%;"></div>
            </div>
          </div>
          <span id="inAppVolText" class="inapp-vol-text">100%</span>
        </div>
        <button class="inapp-btn inapp-btn-icon" onclick="toggleInAppPiP()" title="Picture-in-Picture (I)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><rect x="12" y="9" width="8" height="6" rx="1" fill="currentColor"/>
          </svg>
        </button>
        <button class="inapp-btn inapp-btn-icon" onclick="toggleInAppFullscreen()" title="Plein écran (F)">
          <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</div>

<!-- Modal Roulette KINO (Mode Surprends-moi) -->
<div id="caseModal" class="modal-bg" onclick="if(event.target===this) closeCaseModal()">
  <div class="case-modal-box">
    <div class="case-header">
      <div class="case-title-wrap">
        <span class="case-kicker" id="caseModalKicker">ROULETTE KINO</span>
        <h3 id="caseModalTitle" style="font-size:1.0rem; font-weight:700; color:#fafafa;">Tirage en cours...</h3>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <div id="casePoolChips" style="display:flex; gap:5px; align-items:center; margin-right:6px;">
          <button type="button" class="chip active" data-pool="catalog" style="padding:3px 9px; font-size:0.72rem;" onclick="surpriseMeMedia('catalog')">Catalogue</button>
          <button type="button" class="chip" data-pool="classics" style="padding:3px 9px; font-size:0.72rem;" onclick="surpriseMeMedia('classics')">Classiques</button>
          <button type="button" class="chip" data-pool="watchlist" style="padding:3px 9px; font-size:0.72rem;" onclick="surpriseMeMedia('watchlist')">Ma Liste</button>
          <button type="button" class="chip" data-pool="watched" style="padding:3px 9px; font-size:0.72rem;" onclick="surpriseMeMedia('watched')">Rewatch (Déjà vus)</button>
        </div>
        <button id="caseSoundBtn" class="btn btn-secondary" style="padding:4px 10px; font-size:0.74rem;" onclick="toggleCaseSound()">Son : Activé</button>
        <button id="caseSkipBtn" class="btn btn-secondary" style="padding:4px 10px; font-size:0.74rem;" onclick="skipCaseSpin()">Passer l'animation</button>
        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.74rem;" onclick="closeCaseModal()">Fermer (Échap)</button>
      </div>
    </div>

    <div id="caseRollerViewport" class="case-roller-viewport">
      <div class="case-center-laser"></div>
      <div id="caseRollerStrip" class="case-roller-strip"></div>
    </div>

    <div id="caseWinnerPanel" class="case-winner-panel"></div>
  </div>
</div>

<!-- Modal Importation Watchlist & Films Déjà Vus Letterboxd -->
<div id="letterboxdModal" class="modal-bg" onclick="if(event.target===this) closeLetterboxdModal()">
  <div class="modal" style="max-width:480px; width:92%; padding:20px 22px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
      <div style="display:flex; align-items:center; gap:8px;">
        <svg width="22" height="11" viewBox="0 0 30 12" fill="none">
          <circle cx="6" cy="6" r="5" fill="#ff8000"/>
          <circle cx="15" cy="6" r="5" fill="#00e054"/>
          <circle cx="24" cy="6" r="5" fill="#40bcf4"/>
        </svg>
        <h3 style="font-size:0.96rem; font-weight:600; margin:0;">Letterboxd</h3>
      </div>
      <button onclick="closeLetterboxdModal()" style="background:none; border:none; color:var(--muted); cursor:pointer; font-size:1.1rem; line-height:1; padding:2px 6px;" title="Fermer">✕</button>
    </div>

    <!-- Onglets du modal -->
    <div style="display:flex; gap:6px; border-bottom:1px solid var(--border); padding-bottom:8px; margin-bottom:14px;">
      <button type="button" id="lbxTabBtnProfile" class="chip active" onclick="switchLbxModalTab('profile')" style="padding:4px 11px; font-size:0.76rem;">Profil &amp; Watchlist</button>
      <button type="button" id="lbxTabBtnList" class="chip" onclick="switchLbxModalTab('list')" style="padding:4px 11px; font-size:0.76rem;">Importer une Liste publique</button>
    </div>

    <!-- Section 1 : Profil & Watchlist -->
    <div id="lbxSectionProfile">
      <p style="color:var(--muted); font-size:0.78rem; line-height:1.45; margin:0 0 14px 0;">
        Importe automatiquement votre <strong>Watchlist</strong> (dans <em>Ma Liste</em>) et vos <strong>Films visionnés</strong> (dans <em>Déjà vus</em>, exclus de la roulette).
      </p>
      <div style="display:flex; flex-direction:column; gap:11px;">
        <div>
          <label for="lbxUrlInput" style="font-size:0.75rem; color:var(--text); font-weight:500; display:block; margin-bottom:5px;">Pseudo Letterboxd</label>
          <input type="text" id="lbxUrlInput" placeholder="Ex: passionia ou lien public" style="width:100%; box-sizing:border-box;" onkeydown="if(event.key==='Enter') submitLetterboxdImport()">
        </div>
        <div style="display:flex; align-items:center; justify-content:space-between; padding-top:2px; font-size:0.74rem;">
          <input type="file" id="lbxCsvFileInput" accept=".csv,.txt" style="display:none;" onchange="handleLetterboxdCsvFile(this.files)">
          <button type="button" onclick="document.getElementById('lbxCsvFileInput').click()" style="background:none; border:none; color:var(--muted); text-decoration:underline; cursor:pointer; padding:0; font-size:0.74rem; display:inline-flex; align-items:center; gap:5px;">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span>Importer un export .csv</span>
          </button>
          <div style="display:inline-flex; align-items:center; gap:4px; max-width:210px; overflow:hidden;">
            <span id="lbxCsvFileName" style="font-size:0.74rem; color:#00e054; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;"></span>
            <button type="button" id="lbxCsvClearBtn" onclick="clearLetterboxdCsv(event)" style="display:none; background:none; border:none; color:var(--muted); cursor:pointer; padding:0 3px; font-size:0.75rem;" title="Retirer">✕</button>
          </div>
        </div>
        <div id="lbxImportStatus" style="font-size:0.78rem; line-height:1.35; display:none; padding:8px 10px; border-radius:6px; background:rgba(255,255,255,0.03); border:1px solid var(--border);"></div>
        <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:6px;">
          <button class="btn btn-secondary" style="padding:6px 14px; font-size:0.8rem;" onclick="closeLetterboxdModal()">Annuler</button>
          <button id="lbxSubmitBtn" class="btn" style="padding:6px 16px; font-size:0.8rem;" onclick="submitLetterboxdImport()">Synchroniser</button>
        </div>
      </div>
    </div>

    <!-- Section 2 : Liste publique Letterboxd -->
    <div id="lbxSectionList" style="display:none;">
      <p style="color:var(--muted); font-size:0.78rem; line-height:1.45; margin:0 0 14px 0;">
        Collez l'URL de n'importe quelle liste Letterboxd (ex: <em>Top 250</em>, <em>A24</em>, <em>Films cultes</em>). Elle sera consultable et disponible pour la Roulette !
      </p>
      <div style="display:flex; flex-direction:column; gap:11px;">
        <div>
          <label for="lbxCustomListUrlInput" style="font-size:0.75rem; color:var(--text); font-weight:500; display:block; margin-bottom:5px;">URL de la liste Letterboxd</label>
          <input type="text" id="lbxCustomListUrlInput" placeholder="https://letterboxd.com/username/list/nom-de-la-liste/" style="width:100%; box-sizing:border-box;" onkeydown="if(event.key==='Enter') submitLetterboxdCustomListImport()">
        </div>
        <div id="lbxCustomListStatus" style="font-size:0.78rem; line-height:1.35; display:none; padding:8px 10px; border-radius:6px; background:rgba(255,255,255,0.03); border:1px solid var(--border);"></div>
        <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:6px;">
          <button class="btn btn-secondary" style="padding:6px 14px; font-size:0.8rem;" onclick="closeLetterboxdModal()">Fermer</button>
          <button id="lbxCustomListSubmitBtn" class="btn" style="padding:6px 16px; font-size:0.8rem;" onclick="submitLetterboxdCustomListImport()">Importer cette liste</button>
        </div>

        <div id="lbxSavedListsWrap" style="margin-top:14px; border-top:1px solid var(--border); padding-top:12px; display:none;">
          <div style="font-size:0.74rem; font-weight:600; color:var(--muted); margin-bottom:8px; text-transform:uppercase; letter-spacing:0.04em;">Listes enregistrées</div>
          <div id="lbxSavedListsContainer" style="display:flex; flex-direction:column; gap:6px; max-height:140px; overflow-y:auto;"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Modal Bande-Annonce Intégré -->
<div id="trailerModal" class="modal-bg" onclick="if(event.target===this) closeTrailerModal()">
  <div class="modal" style="max-width:860px; width:94%; padding:16px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
      <h3 id="trailerModalTitle" style="font-size:0.95rem;">Bande-annonce</h3>
      <div style="display:flex; gap:6px; align-items:center;">
        <button id="trailerBtnVf" class="chip active" style="padding:4px 10px; font-size:0.75rem;" onclick="switchTrailerLang('vf')">VF</button>
        <button id="trailerBtnVo" class="chip" style="padding:4px 10px; font-size:0.75rem;" onclick="switchTrailerLang('vo')">VO</button>
        <a id="trailerExternalLink" href="#" target="_blank" class="btn btn-secondary" style="padding:4px 10px; font-size:0.76rem;">YouTube ↗</a>
        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.76rem;" onclick="closeTrailerModal()">Fermer (Échap)</button>
      </div>
    </div>
    <div style="position:relative; width:100%; aspect-ratio:16/9; background:#000; border-radius:6px; overflow:hidden; border:1px solid var(--border);">
      <div id="trailerLoading" style="position:absolute; inset:0; display:none; align-items:center; justify-content:center; color:var(--muted); font-size:0.85rem; background:#09090b; z-index:2;">Chargement de la bande-annonce...</div>
      <video id="trailerVideo" controls playsinline style="width:100%; height:100%; display:none; background:#000;"></video>
      <iframe id="trailerIframe" src="" style="width:100%; height:100%; border:none; display:none;" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>
    </div>
  </div>
</div>

<!-- Modal Config -->
<div id="configModal" class="modal-bg" onclick="if(event.target===this) closeConfig()">
  <div class="modal">
    <h3>Configuration KINO</h3>
    <p id="cfgProviderHelp" style="color:var(--muted); font-size:0.82rem; margin:8px 0 14px; line-height:1.4;">
      Clé API disponible sur <a id="cfgProviderLink" href="https://real-debrid.com/apitoken" target="_blank" style="color:var(--text); text-decoration:underline;">real-debrid.com/apitoken</a>
    </p>
    <div style="display:flex; flex-direction:column; gap:10px;">
      <label style="font-size:0.8rem; color:var(--muted);">Service Débrideur</label>
      <select id="cfgProvider" onchange="onConfigProviderChange()">
        <option value="realdebrid">Real-Debrid (RD+)</option>
        <option value="alldebrid">AllDebrid (AD+)</option>
        <option value="torbox">TorBox (TB+)</option>
        <option value="debridlink">Debrid-Link (DL+)</option>
        <option value="premiumize">Premiumize (PM+)</option>
        <option value="megadebrid">Mega-Debrid (MD+)</option>
      </select>
      <label id="cfgTokenLabel" style="font-size:0.8rem; color:var(--muted);">Clé API Real-Debrid</label>
      <input type="password" id="cfgToken" placeholder="Laisser vide pour conserver la clé actuelle">
      <label style="font-size:0.8rem; color:var(--muted);">Dossier de téléchargement</label>
      <input type="text" id="cfgDir" placeholder="/Users/kaiser/Downloads">
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
        <div style="display:flex; flex-direction:column; gap:6px;">
          <label style="font-size:0.8rem; color:var(--muted);">Langue 1-Clic</label>
          <select id="cfgPrefLang">
            <option value="vf">VF / MULTI</option>
            <option value="vostfr">VOSTFR / VO</option>
          </select>
        </div>
        <div style="display:flex; flex-direction:column; gap:6px;">
          <label style="font-size:0.8rem; color:var(--muted);">Qualité 1-Clic</label>
          <select id="cfgPrefQuality">
            <option value="4k">4K UHD</option>
            <option value="1080p">1080p</option>
          </select>
        </div>
      </div>
      <label style="font-size:0.8rem; color:var(--muted);">Format Vidéo</label>
      <select id="cfgHdrMode">
        <option value="sdr_pref">SDR</option>
        <option value="hdr_native">HDR / Dolby Atmos</option>
      </select>
      <label style="font-size:0.8rem; color:var(--muted);">Mode Audio</label>
      <select id="cfgAudioMode">
        <option value="voice_boost">Boost Voix &amp; Normalisation</option>
        <option value="standard">Audio Standard</option>
      </select>
      <label style="font-size:0.8rem; color:var(--muted);">Nettoyage Cloud Débrideur</label>
      <select id="cfgRdRetention">
        <option value="0">Désactivé</option>
        <option value="1">Supprimer après 24 heures</option>
        <option value="3">Supprimer après 3 jours</option>
        <option value="7">Supprimer après 7 jours</option>
        <option value="14">Supprimer après 14 jours</option>
        <option value="30">Supprimer après 30 jours</option>
      </select>
      <label style="font-size:0.8rem; color:var(--muted);">Lecteur par défaut</label>
      <select id="cfgPlayerMode">
        <option value="kino">Lecteur KINO / IINA</option>
        <option value="integrated">Lecteur intégré</option>
      </select>
      <!-- Section Synchronisation Google Drive -->
      <div style="margin-top:6px; padding:10px 12px; background:var(--surface); border-radius:8px; border:1px solid var(--border);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <span style="font-size:0.82rem; font-weight:600; color:var(--text);">Synchronisation Cloud (Mac ⇄ Windows)</span>
            <p id="gdriveSyncStatus" style="font-size:0.74rem; color:var(--dim); margin:2px 0 0;">Détection automatique du Google Drive...</p>
          </div>
          <button class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem;" onclick="triggerGdriveSync(this)">Synchroniser</button>
        </div>
      </div>
      <!-- Section Synchronisation Trakt.tv -->
      <div style="margin-top:8px; padding:10px 12px; background:var(--surface); border-radius:8px; border:1px solid var(--border);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <span style="font-size:0.82rem; font-weight:600; color:var(--text); display:flex; align-items:center; gap:6px;">
              <span style="color:#ed1c24; font-weight:800;">Trakt.tv</span> Scrobble &amp; Watchlist
            </span>
            <p id="traktSyncStatus" style="font-size:0.74rem; color:var(--dim); margin:2px 0 0;">Non connecté</p>
          </div>
          <div style="display:flex; gap:6px; align-items:center;">
            <button id="traktConnectBtn" class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem;" onclick="toggleTraktAuth(this)">Connecter</button>
            <button id="traktSyncBtn" class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem; display:none;" onclick="triggerTraktSync(this)">Synchroniser</button>
          </div>
        </div>
        <div id="traktAuthBox" style="display:none; margin-top:8px; padding:8px; background:rgba(237,28,36,0.08); border-radius:6px; border:1px dashed rgba(237,28,36,0.3); font-size:0.75rem;">
          <p style="margin:0 0 4px; color:var(--text);">1. Rendez-vous sur <a id="traktAuthUrl" href="https://trakt.tv/activate" target="_blank" style="color:#ed1c24; text-decoration:underline; font-weight:bold;">trakt.tv/activate</a></p>
          <p style="margin:0; color:var(--text);">2. Entrez ce code : <b id="traktUserCode" style="font-size:1rem; letter-spacing:2px; color:#fff; background:#18181b; padding:2px 6px; border-radius:4px;">------</b></p>
          <p id="traktAuthCountdown" style="margin:4px 0 0; color:var(--muted); font-size:0.7rem;">En attente de validation sur votre compte Trakt...</p>
        </div>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:10px;">
        <button class="btn btn-secondary" onclick="closeConfig()">Annuler</button>
        <button class="btn" onclick="saveConfig()">Enregistrer</button>
      </div>
    </div>
  </div>
</div>

<!-- Modal Raccourcis Clavier -->
<div id="shortcutsModal" class="shortcuts-modal" onclick="if(event.target===this) toggleShortcutsModal()">
  <div class="shortcuts-content">
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border); padding-bottom:12px;">
      <h3 style="font-size:1rem; font-weight:600;">⌨ Raccourcis Clavier KINO</h3>
      <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick="toggleShortcutsModal()">Fermer (Échap)</button>
    </div>
    <div class="shortcuts-grid">
      <div class="shortcut-row"><span>Lecture / Pause</span><kbd>Espace</kbd></div>
      <div class="shortcut-row"><span>Plein écran</span><kbd>F</kbd></div>
      <div class="shortcut-row"><span>Reculer / Avancer 10s</span><div><kbd>←</kbd> <kbd>→</kbd></div></div>
      <div class="shortcut-row"><span>Volume +/-</span><div><kbd>↑</kbd> <kbd>↓</kbd></div></div>
      <div class="shortcut-row"><span>Activer / Couper son</span><kbd>M</kbd></div>
      <div class="shortcut-row"><span>Sous-titres On / Off</span><kbd>C</kbd></div>
      <div class="shortcut-row"><span>Synchro sous-titres (±250ms)</span><div><kbd>Z</kbd> <kbd>X</kbd></div></div>
      <div class="shortcut-row"><span>Vitesse de lecture</span><div><kbd>[</kbd> <kbd>]</kbd></div></div>
      <div class="shortcut-row"><span>Bascule MPV (Flux 4K HDR)</span><kbd>E</kbd></div>
      <div class="shortcut-row"><span>Épisode suivant</span><kbd>N</kbd></div>
      <div class="shortcut-row"><span>Passer l'intro (+85s)</span><kbd>S</kbd></div>
      <div class="shortcut-row"><span>Picture-in-Picture</span><kbd>I</kbd></div>
      <div class="shortcut-row"><span>Focus recherche</span><div><kbd>⌘K</kbd> / <kbd>/</kbd></div></div>
    </div>
  </div>
</div>

<!-- Modal Télécommande Smartphone -->
<div id="remoteModal" class="modal-bg" onclick="if(event.target===this) closeRemoteModal()">
  <div class="modal" style="max-width:440px; text-align:center; padding:22px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h3 style="font-size:1.05rem; display:flex; align-items:center; gap:8px; margin:0;">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
        <span>KINO Remote</span>
      </h3>
      <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick="closeRemoteModal()">✕</button>
    </div>
    <p style="color:var(--muted); font-size:0.8rem; line-height:1.4; margin:0 0 14px;">
      Scannez ce QR Code avec votre smartphone connecté au même Wi-Fi pour contrôler la lecture, le volume et naviguer depuis votre canapé.
    </p>
    <div id="remoteQrContainer" style="display:flex; justify-content:center; align-items:center; margin:0 auto 14px; background:#fff; padding:12px; border-radius:12px; width:220px; height:220px; box-shadow:0 8px 24px rgba(0,0,0,0.5);">
      <div style="color:#666; font-size:0.8rem;">Génération du QR Code...</div>
    </div>
    <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:8px 12px; display:flex; justify-content:space-between; align-items:center; gap:8px;">
      <span id="remoteDirectUrl" style="font-size:0.78rem; color:var(--dim); word-break:break-all; text-align:left; font-family:monospace;">http://...</span>
      <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.72rem; white-space:nowrap;" onclick="copyRemoteUrl()">Copier</button>
    </div>
  </div>
</div>

<!-- Modal Watch Party P2P -->
<div id="watchPartyModal" class="modal-bg" onclick="if(event.target===this) closeWatchPartyModal()">
  <div class="modal" style="max-width:480px; padding:20px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
      <h3 style="font-size:1.05rem; display:flex; align-items:center; gap:8px; margin:0;">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span>Mode Watch Party P2P</span>
      </h3>
      <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick="closeWatchPartyModal()">✕</button>
    </div>
    <p style="color:var(--muted); font-size:0.8rem; line-height:1.4; margin:0 0 16px;">
      Regardez vos films et séries en parfaite synchronisation avec vos amis. Play, pause et temps de lecture calés à la seconde près sans serveur tiers (P2P direct).
    </p>

    <!-- Zone Non Connecté -->
    <div id="wpSetupView">
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:16px;">
        <button class="btn" style="padding:10px 14px; font-size:0.82rem; font-weight:600;" onclick="createWatchPartyRoom()">
          Créer une Salle
        </button>
        <button class="btn btn-secondary" style="padding:10px 14px; font-size:0.82rem;" onclick="showJoinWatchParty()">
          Rejoindre une Salle
        </button>
      </div>
      <div id="wpJoinBox" style="display:none; padding:12px; background:var(--surface); border-radius:8px; border:1px solid var(--border);">
        <label style="font-size:0.76rem; color:var(--muted); display:block; margin-bottom:6px;">Entrez le code de salle :</label>
        <div style="display:flex; gap:8px;">
          <input type="text" id="wpJoinCodeInput" placeholder="Ex: KINO9X" maxlength="12" style="text-transform:uppercase; font-weight:bold; letter-spacing:2px; font-size:0.95rem; text-align:center;">
          <button class="btn" style="padding:6px 14px; font-size:0.8rem;" onclick="joinWatchPartyRoom()">Rejoindre</button>
        </div>
      </div>
    </div>

    <!-- Zone Connecté / Salle Active -->
    <div id="wpActiveView" style="display:none;">
      <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px; margin-bottom:14px; text-align:center;">
        <span style="font-size:0.72rem; color:var(--dim); text-transform:uppercase; letter-spacing:1px;">Code de votre Salle</span>
        <div style="display:flex; justify-content:center; align-items:center; gap:10px; margin:6px 0;">
          <span id="wpRoomCodeDisplay" style="font-size:1.6rem; font-weight:800; letter-spacing:4px; color:#4ade80; font-family:monospace;">------</span>
          <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.72rem;" onclick="copyWpRoomCode()">Copier</button>
        </div>
        <p style="font-size:0.72rem; color:var(--muted); margin:0;">Partagez ce code à vos amis pour synchroniser vos lecteurs instantanément.</p>
      </div>

      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; font-size:0.8rem;">
        <span style="color:var(--muted);">Statut : <b id="wpConnectionStatus" style="color:#4ade80;">Connecté</b></span>
        <span style="color:var(--muted);">Pairs connectés : <b id="wpPeersCount" style="color:var(--text);">1</b></span>
      </div>

      <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
        <button class="btn btn-secondary" style="padding:6px 12px; font-size:0.78rem; color:#ef4444;" onclick="leaveWatchPartyRoom()">Quitter la salle</button>
      </div>
    </div>
  </div>
</div>

<!-- Modal Gestionnaire d'Add-ons -->
<div id="addonsModal" class="modal-bg" onclick="if(event.target===this) closeAddonsModal()">
  <div class="modal" style="max-width:640px; padding:20px; max-height:85vh; display:flex; flex-direction:column;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h3 style="font-size:1.05rem; display:flex; align-items:center; gap:8px; margin:0;">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>
        <span>Add-ons &amp; Scrapers Communautaires</span>
      </h3>
      <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick="closeAddonsModal()">✕</button>
    </div>
    <p style="color:var(--muted); font-size:0.8rem; line-height:1.4; margin:0 0 14px;">
      Activez ou ajoutez des indexeurs et scrapers communautaires pour démultiplier les sources de recherche (Nyaa Anime VOSTFR, YggTorrent, Sharewood, etc.).
    </p>

    <!-- Bouton d'importation d'add-on -->
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; padding:8px 12px; background:var(--surface); border-radius:8px; border:1px solid var(--border);">
      <span style="font-size:0.78rem; color:var(--dim);">Installer un add-on externe (.kino / .json)</span>
      <div>
        <input type="file" id="addonFileInput" accept=".kino,.json,.zip" style="display:none;" onchange="uploadAddonFile(this)">
        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.76rem;" onclick="document.getElementById('addonFileInput').click()">+ Installer un Add-on</button>
      </div>
    </div>

    <!-- Liste des addons avec scroll -->
    <div id="addonsListContainer" style="overflow-y:auto; flex:1; display:flex; flex-direction:column; gap:8px; padding-right:4px;">
      <div style="color:var(--dim); font-size:0.8rem; text-align:center; padding:20px;">Chargement des add-ons...</div>
    </div>
  </div>
</div>

<script>
/* QRCode.js local embed */
var QRCode;!function(){function a(a){this.mode=c.MODE_8BIT_BYTE,this.data=a,this.parsedData=[];for(var b=[],d=0,e=this.data.length;e>d;d++){var f=this.data.charCodeAt(d);f>65536?(b[0]=240|(1835008&f)>>>18,b[1]=128|(258048&f)>>>12,b[2]=128|(4032&f)>>>6,b[3]=128|63&f):f>2048?(b[0]=224|(61440&f)>>>12,b[1]=128|(4032&f)>>>6,b[2]=128|63&f):f>128?(b[0]=192|(1984&f)>>>6,b[1]=128|63&f):b[0]=f,this.parsedData=this.parsedData.concat(b)}this.parsedData.length!=this.data.length&&(this.parsedData.unshift(191),this.parsedData.unshift(187),this.parsedData.unshift(239))}function b(a,b){this.typeNumber=a,this.errorCorrectLevel=b,this.modules=null,this.moduleCount=0,this.dataCache=null,this.dataList=[]}function i(a,b){if(void 0==a.length)throw new Error(a.length+"/"+b);for(var c=0;c<a.length&&0==a[c];)c++;this.num=new Array(a.length-c+b);for(var d=0;d<a.length-c;d++)this.num[d]=a[d+c]}function j(a,b){this.totalCount=a,this.dataCount=b}function k(){this.buffer=[],this.length=0}function m(){return"undefined"!=typeof CanvasRenderingContext2D}function n(){var a=!1,b=navigator.userAgent;return/android/i.test(b)&&(a=!0,aMat=b.toString().match(/android ([0-9]\.[0-9])/i),aMat&&aMat[1]&&(a=parseFloat(aMat[1]))),a}function r(a,b){for(var c=1,e=s(a),f=0,g=l.length;g>=f;f++){var h=0;switch(b){case d.L:h=l[f][0];break;case d.M:h=l[f][1];break;case d.Q:h=l[f][2];break;case d.H:h=l[f][3]}if(h>=e)break;c++}if(c>l.length)throw new Error("Too long data");return c}function s(a){var b=encodeURI(a).toString().replace(/\%[0-9a-fA-F]{2}/g,"a");return b.length+(b.length!=a?3:0)}a.prototype={getLength:function(){return this.parsedData.length},write:function(a){for(var b=0,c=this.parsedData.length;c>b;b++)a.put(this.parsedData[b],8)}},b.prototype={addData:function(b){var c=new a(b);this.dataList.push(c),this.dataCache=null},isDark:function(a,b){if(0>a||this.moduleCount<=a||0>b||this.moduleCount<=b)throw new Error(a+","+b);return this.modules[a][b]},getModuleCount:function(){return this.moduleCount},make:function(){this.makeImpl(!1,this.getBestMaskPattern())},makeImpl:function(a,c){this.moduleCount=4*this.typeNumber+17,this.modules=new Array(this.moduleCount);for(var d=0;d<this.moduleCount;d++){this.modules[d]=new Array(this.moduleCount);for(var e=0;e<this.moduleCount;e++)this.modules[d][e]=null}this.setupPositionProbePattern(0,0),this.setupPositionProbePattern(this.moduleCount-7,0),this.setupPositionProbePattern(0,this.moduleCount-7),this.setupPositionAdjustPattern(),this.setupTimingPattern(),this.setupTypeInfo(a,c),this.typeNumber>=7&&this.setupTypeNumber(a),null==this.dataCache&&(this.dataCache=b.createData(this.typeNumber,this.errorCorrectLevel,this.dataList)),this.mapData(this.dataCache,c)},setupPositionProbePattern:function(a,b){for(var c=-1;7>=c;c++)if(!(-1>=a+c||this.moduleCount<=a+c))for(var d=-1;7>=d;d++)-1>=b+d||this.moduleCount<=b+d||(this.modules[a+c][b+d]=c>=0&&6>=c&&(0==d||6==d)||d>=0&&6>=d&&(0==c||6==c)||c>=2&&4>=c&&d>=2&&4>=d?!0:!1)},getBestMaskPattern:function(){for(var a=0,b=0,c=0;8>c;c++){this.makeImpl(!0,c);var d=f.getLostPoint(this);(0==c||a>d)&&(a=d,b=c)}return b},createMovieClip:function(a,b,c){var d=a.createEmptyMovieClip(b,c),e=1;this.make();for(var f=0;f<this.modules.length;f++)for(var g=f*e,h=0;h<this.modules[f].length;h++){var i=h*e,j=this.modules[f][h];j&&(d.beginFill(0,100),d.moveTo(i,g),d.lineTo(i+e,g),d.lineTo(i+e,g+e),d.lineTo(i,g+e),d.endFill())}return d},setupTimingPattern:function(){for(var a=8;a<this.moduleCount-8;a++)null==this.modules[a][6]&&(this.modules[a][6]=0==a%2);for(var b=8;b<this.moduleCount-8;b++)null==this.modules[6][b]&&(this.modules[6][b]=0==b%2)},setupPositionAdjustPattern:function(){for(var a=f.getPatternPosition(this.typeNumber),b=0;b<a.length;b++)for(var c=0;c<a.length;c++){var d=a[b],e=a[c];if(null==this.modules[d][e])for(var g=-2;2>=g;g++)for(var h=-2;2>=h;h++)this.modules[d+g][e+h]=-2==g||2==g||-2==h||2==h||0==g&&0==h?!0:!1}},setupTypeNumber:function(a){for(var b=f.getBCHTypeNumber(this.typeNumber),c=0;18>c;c++){var d=!a&&1==(1&b>>c);this.modules[Math.floor(c/3)][c%3+this.moduleCount-8-3]=d}for(var c=0;18>c;c++){var d=!a&&1==(1&b>>c);this.modules[c%3+this.moduleCount-8-3][Math.floor(c/3)]=d}},setupTypeInfo:function(a,b){for(var c=this.errorCorrectLevel<<3|b,d=f.getBCHTypeInfo(c),e=0;15>e;e++){var g=!a&&1==(1&d>>e);6>e?this.modules[e][8]=g:8>e?this.modules[e+1][8]=g:this.modules[this.moduleCount-15+e][8]=g}for(var e=0;15>e;e++){var g=!a&&1==(1&d>>e);8>e?this.modules[8][this.moduleCount-e-1]=g:9>e?this.modules[8][15-e-1+1]=g:this.modules[8][15-e-1]=g}this.modules[this.moduleCount-8][8]=!a},mapData:function(a,b){for(var c=-1,d=this.moduleCount-1,e=7,g=0,h=this.moduleCount-1;h>0;h-=2)for(6==h&&h--;;){for(var i=0;2>i;i++)if(null==this.modules[d][h-i]){var j=!1;g<a.length&&(j=1==(1&a[g]>>>e));var k=f.getMask(b,d,h-i);k&&(j=!j),this.modules[d][h-i]=j,e--,-1==e&&(g++,e=7)}if(d+=c,0>d||this.moduleCount<=d){d-=c,c=-c;break}}}},b.PAD0=236,b.PAD1=17,b.createData=function(a,c,d){for(var e=j.getRSBlocks(a,c),g=new k,h=0;h<d.length;h++){var i=d[h];g.put(i.mode,4),g.put(i.getLength(),f.getLengthInBits(i.mode,a)),i.write(g)}for(var l=0,h=0;h<e.length;h++)l+=e[h].dataCount;if(g.getLengthInBits()>8*l)throw new Error("code length overflow. ("+g.getLengthInBits()+">"+8*l+")");for(g.getLengthInBits()+4<=8*l&&g.put(0,4);0!=g.getLengthInBits()%8;)g.putBit(!1);for(;;){if(g.getLengthInBits()>=8*l)break;if(g.put(b.PAD0,8),g.getLengthInBits()>=8*l)break;g.put(b.PAD1,8)}return b.createBytes(g,e)},b.createBytes=function(a,b){for(var c=0,d=0,e=0,g=new Array(b.length),h=new Array(b.length),j=0;j<b.length;j++){var k=b[j].dataCount,l=b[j].totalCount-k;d=Math.max(d,k),e=Math.max(e,l),g[j]=new Array(k);for(var m=0;m<g[j].length;m++)g[j][m]=255&a.buffer[m+c];c+=k;var n=f.getErrorCorrectPolynomial(l),o=new i(g[j],n.getLength()-1),p=o.mod(n);h[j]=new Array(n.getLength()-1);for(var m=0;m<h[j].length;m++){var q=m+p.getLength()-h[j].length;h[j][m]=q>=0?p.get(q):0}}for(var r=0,m=0;m<b.length;m++)r+=b[m].totalCount;for(var s=new Array(r),t=0,m=0;d>m;m++)for(var j=0;j<b.length;j++)m<g[j].length&&(s[t++]=g[j][m]);for(var m=0;e>m;m++)for(var j=0;j<b.length;j++)m<h[j].length&&(s[t++]=h[j][m]);return s};for(var c={MODE_NUMBER:1,MODE_ALPHA_NUM:2,MODE_8BIT_BYTE:4,MODE_KANJI:8},d={L:1,M:0,Q:3,H:2},e={PATTERN000:0,PATTERN001:1,PATTERN010:2,PATTERN011:3,PATTERN100:4,PATTERN101:5,PATTERN110:6,PATTERN111:7},f={PATTERN_POSITION_TABLE:[[],[6,18],[6,22],[6,26],[6,30],[6,34],[6,22,38],[6,24,42],[6,26,46],[6,28,50],[6,30,54],[6,32,58],[6,34,62],[6,26,46,66],[6,26,48,70],[6,26,50,74],[6,30,54,78],[6,30,56,82],[6,30,58,86],[6,34,62,90],[6,28,50,72,94],[6,26,50,74,98],[6,30,54,78,102],[6,28,54,80,106],[6,32,58,84,110],[6,30,58,86,114],[6,34,62,90,118],[6,26,50,74,98,122],[6,30,54,78,102,126],[6,26,52,78,104,130],[6,30,56,82,108,134],[6,34,60,86,112,138],[6,30,58,86,114,142],[6,34,62,90,118,146],[6,30,54,78,102,126,150],[6,24,50,76,102,128,154],[6,28,54,80,106,132,158],[6,32,58,84,110,136,162],[6,26,54,82,110,138,166],[6,30,58,86,114,142,170]],G15:1335,G18:7973,G15_MASK:21522,getBCHTypeInfo:function(a){for(var b=a<<10;f.getBCHDigit(b)-f.getBCHDigit(f.G15)>=0;)b^=f.G15<<f.getBCHDigit(b)-f.getBCHDigit(f.G15);return(a<<10|b)^f.G15_MASK},getBCHTypeNumber:function(a){for(var b=a<<12;f.getBCHDigit(b)-f.getBCHDigit(f.G18)>=0;)b^=f.G18<<f.getBCHDigit(b)-f.getBCHDigit(f.G18);return a<<12|b},getBCHDigit:function(a){for(var b=0;0!=a;)b++,a>>>=1;return b},getPatternPosition:function(a){return f.PATTERN_POSITION_TABLE[a-1]},getMask:function(a,b,c){switch(a){case e.PATTERN000:return 0==(b+c)%2;case e.PATTERN001:return 0==b%2;case e.PATTERN010:return 0==c%3;case e.PATTERN011:return 0==(b+c)%3;case e.PATTERN100:return 0==(Math.floor(b/2)+Math.floor(c/3))%2;case e.PATTERN101:return 0==b*c%2+b*c%3;case e.PATTERN110:return 0==(b*c%2+b*c%3)%2;case e.PATTERN111:return 0==(b*c%3+(b+c)%2)%2;default:throw new Error("bad maskPattern:"+a)}},getErrorCorrectPolynomial:function(a){for(var b=new i([1],0),c=0;a>c;c++)b=b.multiply(new i([1,g.gexp(c)],0));return b},getLengthInBits:function(a,b){if(b>=1&&10>b)switch(a){case c.MODE_NUMBER:return 10;case c.MODE_ALPHA_NUM:return 9;case c.MODE_8BIT_BYTE:return 8;case c.MODE_KANJI:return 8;default:throw new Error("mode:"+a)}else if(27>b)switch(a){case c.MODE_NUMBER:return 12;case c.MODE_ALPHA_NUM:return 11;case c.MODE_8BIT_BYTE:return 16;case c.MODE_KANJI:return 10;default:throw new Error("mode:"+a)}else{if(!(41>b))throw new Error("type:"+b);switch(a){case c.MODE_NUMBER:return 14;case c.MODE_ALPHA_NUM:return 13;case c.MODE_8BIT_BYTE:return 16;case c.MODE_KANJI:return 12;default:throw new Error("mode:"+a)}}},getLostPoint:function(a){for(var b=a.getModuleCount(),c=0,d=0;b>d;d++)for(var e=0;b>e;e++){for(var f=0,g=a.isDark(d,e),h=-1;1>=h;h++)if(!(0>d+h||d+h>=b))for(var i=-1;1>=i;i++)0>e+i||e+i>=b||(0!=h||0!=i)&&g==a.isDark(d+h,e+i)&&f++;f>5&&(c+=3+f-5)}for(var d=0;b-1>d;d++)for(var e=0;b-1>e;e++){var j=0;a.isDark(d,e)&&j++,a.isDark(d+1,e)&&j++,a.isDark(d,e+1)&&j++,a.isDark(d+1,e+1)&&j++,(0==j||4==j)&&(c+=3)}for(var d=0;b>d;d++)for(var e=0;b-6>e;e++)a.isDark(d,e)&&!a.isDark(d,e+1)&&a.isDark(d,e+2)&&a.isDark(d,e+3)&&a.isDark(d,e+4)&&!a.isDark(d,e+5)&&a.isDark(d,e+6)&&(c+=40);for(var e=0;b>e;e++)for(var d=0;b-6>d;d++)a.isDark(d,e)&&!a.isDark(d+1,e)&&a.isDark(d+2,e)&&a.isDark(d+3,e)&&a.isDark(d+4,e)&&!a.isDark(d+5,e)&&a.isDark(d+6,e)&&(c+=40);for(var k=0,e=0;b>e;e++)for(var d=0;b>d;d++)a.isDark(d,e)&&k++;var l=Math.abs(100*k/b/b-50)/5;return c+=10*l}},g={glog:function(a){if(1>a)throw new Error("glog("+a+")");return g.LOG_TABLE[a]},gexp:function(a){for(;0>a;)a+=255;for(;a>=256;)a-=255;return g.EXP_TABLE[a]},EXP_TABLE:new Array(256),LOG_TABLE:new Array(256)},h=0;8>h;h++)g.EXP_TABLE[h]=1<<h;for(var h=8;256>h;h++)g.EXP_TABLE[h]=g.EXP_TABLE[h-4]^g.EXP_TABLE[h-5]^g.EXP_TABLE[h-6]^g.EXP_TABLE[h-8];for(var h=0;255>h;h++)g.LOG_TABLE[g.EXP_TABLE[h]]=h;i.prototype={get:function(a){return this.num[a]},getLength:function(){return this.num.length},multiply:function(a){for(var b=new Array(this.getLength()+a.getLength()-1),c=0;c<this.getLength();c++)for(var d=0;d<a.getLength();d++)b[c+d]^=g.gexp(g.glog(this.get(c))+g.glog(a.get(d)));return new i(b,0)},mod:function(a){if(this.getLength()-a.getLength()<0)return this;for(var b=g.glog(this.get(0))-g.glog(a.get(0)),c=new Array(this.getLength()),d=0;d<this.getLength();d++)c[d]=this.get(d);for(var d=0;d<a.getLength();d++)c[d]^=g.gexp(g.glog(a.get(d))+b);return new i(c,0).mod(a)}},j.RS_BLOCK_TABLE=[[1,26,19],[1,26,16],[1,26,13],[1,26,9],[1,44,34],[1,44,28],[1,44,22],[1,44,16],[1,70,55],[1,70,44],[2,35,17],[2,35,13],[1,100,80],[2,50,32],[2,50,24],[4,25,9],[1,134,108],[2,67,43],[2,33,15,2,34,16],[2,33,11,2,34,12],[2,86,68],[4,43,27],[4,43,19],[4,43,15],[2,98,78],[4,49,31],[2,32,14,4,33,15],[4,39,13,1,40,14],[2,121,97],[2,60,38,2,61,39],[4,40,18,2,41,19],[4,40,14,2,41,15],[2,146,116],[3,58,36,2,59,37],[4,36,16,4,37,17],[4,36,12,4,37,13],[2,86,68,2,87,69],[4,69,43,1,70,44],[6,43,19,2,44,20],[6,43,15,2,44,16],[4,101,81],[1,80,50,4,81,51],[4,50,22,4,51,23],[3,36,12,8,37,13],[2,116,92,2,117,93],[6,58,36,2,59,37],[4,46,20,6,47,21],[7,42,14,4,43,15],[4,133,107],[8,59,37,1,60,38],[8,44,20,4,45,21],[12,33,11,4,34,12],[3,145,115,1,146,116],[4,64,40,5,65,41],[11,36,16,5,37,17],[11,36,12,5,37,13],[5,109,87,1,110,88],[5,65,41,5,66,42],[5,54,24,7,55,25],[11,36,12],[5,122,98,1,123,99],[7,73,45,3,74,46],[15,43,19,2,44,20],[3,45,15,13,46,16],[1,135,107,5,136,108],[10,74,46,1,75,47],[1,50,22,15,51,23],[2,42,14,17,43,15],[5,150,120,1,151,121],[9,69,43,4,70,44],[17,50,22,1,51,23],[2,42,14,19,43,15],[3,141,113,4,142,114],[3,70,44,11,71,45],[17,47,21,4,48,22],[9,39,13,16,40,14],[3,135,107,5,136,108],[3,67,41,13,68,42],[15,54,24,5,55,25],[15,43,15,10,44,16],[4,144,116,4,145,117],[17,68,42],[17,50,22,6,51,23],[19,46,16,6,47,17],[2,139,111,7,140,112],[17,74,46],[7,54,24,16,55,25],[34,37,13],[4,151,121,5,152,122],[4,75,47,14,76,48],[11,54,24,14,55,25],[16,45,15,14,46,16],[6,147,117,4,148,118],[6,73,45,14,74,46],[11,54,24,16,55,25],[30,46,16,2,47,17],[8,132,106,4,133,107],[8,75,47,13,76,48],[7,54,24,22,55,25],[22,45,15,13,46,16],[10,142,114,2,143,115],[19,74,46,4,75,47],[28,50,22,6,51,23],[33,46,16,4,47,17],[8,152,122,4,153,123],[22,73,45,3,74,46],[8,53,23,26,54,24],[12,45,15,28,46,16],[3,147,117,10,148,118],[3,73,45,23,74,46],[4,54,24,31,55,25],[11,45,15,31,46,16],[7,146,116,7,147,117],[21,73,45,7,74,46],[1,53,23,37,54,24],[19,45,15,26,46,16],[5,145,115,10,146,116],[19,75,47,10,76,48],[15,54,24,25,55,25],[23,45,15,25,46,16],[13,145,115,3,146,116],[2,74,46,29,75,47],[42,54,24,1,55,25],[23,45,15,28,46,16],[17,145,115],[10,74,46,23,75,47],[10,54,24,35,55,25],[19,45,15,35,46,16],[17,145,115,1,146,116],[14,74,46,21,75,47],[29,54,24,19,55,25],[11,45,15,46,46,16],[13,145,115,6,146,116],[14,74,46,23,75,47],[44,54,24,7,55,25],[59,46,16,1,47,17],[12,151,121,7,152,122],[12,75,47,26,76,48],[39,54,24,14,55,25],[22,45,15,41,46,16],[6,151,121,14,152,122],[6,75,47,34,76,48],[46,54,24,10,55,25],[2,45,15,64,46,16],[17,152,122,4,153,123],[29,74,46,14,75,47],[49,54,24,10,55,25],[24,45,15,46,46,16],[4,152,122,18,153,123],[13,74,46,32,75,47],[48,54,24,14,55,25],[42,45,15,32,46,16],[20,147,117,4,148,118],[40,75,47,7,76,48],[43,54,24,22,55,25],[10,45,15,67,46,16],[19,148,118,6,149,119],[18,75,47,31,76,48],[34,54,24,34,55,25],[20,45,15,61,46,16]],j.getRSBlocks=function(a,b){var c=j.getRsBlockTable(a,b);if(void 0==c)throw new Error("bad rs block @ typeNumber:"+a+"/errorCorrectLevel:"+b);for(var d=c.length/3,e=[],f=0;d>f;f++)for(var g=c[3*f+0],h=c[3*f+1],i=c[3*f+2],k=0;g>k;k++)e.push(new j(h,i));return e},j.getRsBlockTable=function(a,b){switch(b){case d.L:return j.RS_BLOCK_TABLE[4*(a-1)+0];case d.M:return j.RS_BLOCK_TABLE[4*(a-1)+1];case d.Q:return j.RS_BLOCK_TABLE[4*(a-1)+2];case d.H:return j.RS_BLOCK_TABLE[4*(a-1)+3];default:return void 0}},k.prototype={get:function(a){var b=Math.floor(a/8);return 1==(1&this.buffer[b]>>>7-a%8)},put:function(a,b){for(var c=0;b>c;c++)this.putBit(1==(1&a>>>b-c-1))},getLengthInBits:function(){return this.length},putBit:function(a){var b=Math.floor(this.length/8);this.buffer.length<=b&&this.buffer.push(0),a&&(this.buffer[b]|=128>>>this.length%8),this.length++}};var l=[[17,14,11,7],[32,26,20,14],[53,42,32,24],[78,62,46,34],[106,84,60,44],[134,106,74,58],[154,122,86,64],[192,152,108,84],[230,180,130,98],[271,213,151,119],[321,251,177,137],[367,287,203,155],[425,331,241,177],[458,362,258,194],[520,412,292,220],[586,450,322,250],[644,504,364,280],[718,560,394,310],[792,624,442,338],[858,666,482,382],[929,711,509,403],[1003,779,565,439],[1091,857,611,461],[1171,911,661,511],[1273,997,715,535],[1367,1059,751,593],[1465,1125,805,625],[1528,1190,868,658],[1628,1264,908,698],[1732,1370,982,742],[1840,1452,1030,790],[1952,1538,1112,842],[2068,1628,1168,898],[2188,1722,1228,958],[2303,1809,1283,983],[2431,1911,1351,1051],[2563,1989,1423,1093],[2699,2099,1499,1139],[2809,2213,1579,1219],[2953,2331,1663,1273]],o=function(){var a=function(a,b){this._el=a,this._htOption=b};return a.prototype.draw=function(a){function g(a,b){var c=document.createElementNS("http://www.w3.org/2000/svg",a);for(var d in b)b.hasOwnProperty(d)&&c.setAttribute(d,b[d]);return c}var b=this._htOption,c=this._el,d=a.getModuleCount();Math.floor(b.width/d),Math.floor(b.height/d),this.clear();var h=g("svg",{viewBox:"0 0 "+String(d)+" "+String(d),width:"100%",height:"100%",fill:b.colorLight});h.setAttributeNS("http://www.w3.org/2000/xmlns/","xmlns:xlink","http://www.w3.org/1999/xlink"),c.appendChild(h),h.appendChild(g("rect",{fill:b.colorDark,width:"1",height:"1",id:"template"}));for(var i=0;d>i;i++)for(var j=0;d>j;j++)if(a.isDark(i,j)){var k=g("use",{x:String(i),y:String(j)});k.setAttributeNS("http://www.w3.org/1999/xlink","href","#template"),h.appendChild(k)}},a.prototype.clear=function(){for(;this._el.hasChildNodes();)this._el.removeChild(this._el.lastChild)},a}(),p="svg"===document.documentElement.tagName.toLowerCase(),q=p?o:m()?function(){function a(){this._elImage.src=this._elCanvas.toDataURL("image/png"),this._elImage.style.display="block",this._elCanvas.style.display="none"}function d(a,b){var c=this;if(c._fFail=b,c._fSuccess=a,null===c._bSupportDataURI){var d=document.createElement("img"),e=function(){c._bSupportDataURI=!1,c._fFail&&_fFail.call(c)},f=function(){c._bSupportDataURI=!0,c._fSuccess&&c._fSuccess.call(c)};return d.onabort=e,d.onerror=e,d.onload=f,d.src="data:image/gif;base64,iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQI12P4//8/w38GIAXDIBKE0DHxgljNBAAO9TXL0Y4OHwAAAABJRU5ErkJggg==",void 0}c._bSupportDataURI===!0&&c._fSuccess?c._fSuccess.call(c):c._bSupportDataURI===!1&&c._fFail&&c._fFail.call(c)}if(this._android&&this._android<=2.1){var b=1/window.devicePixelRatio,c=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(a,d,e,f,g,h,i,j){if("nodeName"in a&&/img/i.test(a.nodeName))for(var l=arguments.length-1;l>=1;l--)arguments[l]=arguments[l]*b;else"undefined"==typeof j&&(arguments[1]*=b,arguments[2]*=b,arguments[3]*=b,arguments[4]*=b);c.apply(this,arguments)}}var e=function(a,b){this._bIsPainted=!1,this._android=n(),this._htOption=b,this._elCanvas=document.createElement("canvas"),this._elCanvas.width=b.width,this._elCanvas.height=b.height,a.appendChild(this._elCanvas),this._el=a,this._oContext=this._elCanvas.getContext("2d"),this._bIsPainted=!1,this._elImage=document.createElement("img"),this._elImage.style.display="none",this._el.appendChild(this._elImage),this._bSupportDataURI=null};return e.prototype.draw=function(a){var b=this._elImage,c=this._oContext,d=this._htOption,e=a.getModuleCount(),f=d.width/e,g=d.height/e,h=Math.round(f),i=Math.round(g);b.style.display="none",this.clear();for(var j=0;e>j;j++)for(var k=0;e>k;k++){var l=a.isDark(j,k),m=k*f,n=j*g;c.strokeStyle=l?d.colorDark:d.colorLight,c.lineWidth=1,c.fillStyle=l?d.colorDark:d.colorLight,c.fillRect(m,n,f,g),c.strokeRect(Math.floor(m)+.5,Math.floor(n)+.5,h,i),c.strokeRect(Math.ceil(m)-.5,Math.ceil(n)-.5,h,i)}this._bIsPainted=!0},e.prototype.makeImage=function(){this._bIsPainted&&d.call(this,a)},e.prototype.isPainted=function(){return this._bIsPainted},e.prototype.clear=function(){this._oContext.clearRect(0,0,this._elCanvas.width,this._elCanvas.height),this._bIsPainted=!1},e.prototype.round=function(a){return a?Math.floor(1e3*a)/1e3:a},e}():function(){var a=function(a,b){this._el=a,this._htOption=b};return a.prototype.draw=function(a){for(var b=this._htOption,c=this._el,d=a.getModuleCount(),e=Math.floor(b.width/d),f=Math.floor(b.height/d),g=['<table style="border:0;border-collapse:collapse;">'],h=0;d>h;h++){g.push("<tr>");for(var i=0;d>i;i++)g.push('<td style="border:0;border-collapse:collapse;padding:0;margin:0;width:'+e+"px;height:"+f+"px;background-color:"+(a.isDark(h,i)?b.colorDark:b.colorLight)+';"></td>');g.push("</tr>")}g.push("</table>"),c.innerHTML=g.join("");var j=c.childNodes[0],k=(b.width-j.offsetWidth)/2,l=(b.height-j.offsetHeight)/2;k>0&&l>0&&(j.style.margin=l+"px "+k+"px")},a.prototype.clear=function(){this._el.innerHTML=""},a}();QRCode=function(a,b){if(this._htOption={width:256,height:256,typeNumber:4,colorDark:"#000000",colorLight:"#ffffff",correctLevel:d.H},"string"==typeof b&&(b={text:b}),b)for(var c in b)this._htOption[c]=b[c];"string"==typeof a&&(a=document.getElementById(a)),this._android=n(),this._el=a,this._oQRCode=null,this._oDrawing=new q(this._el,this._htOption),this._htOption.text&&this.makeCode(this._htOption.text)},QRCode.prototype.makeCode=function(a){this._oQRCode=new b(r(a,this._htOption.correctLevel),this._htOption.correctLevel),this._oQRCode.addData(a),this._oQRCode.make(),this._el.title=a,this._oDrawing.draw(this._oQRCode),this.makeImage()},QRCode.prototype.makeImage=function(){"function"==typeof this._oDrawing.makeImage&&(!this._android||this._android>=3)&&this._oDrawing.makeImage()},QRCode.prototype.clear=function(){this._oDrawing.clear()},QRCode.CorrectLevel=d}();
</script>
<script src="https://unpkg.com/peerjs@1.5.4/dist/peerjs.min.js"></script>
<script>
let allTorrents = [];
let activeFilter = '';
let activeGenre = '';
let searchDebounceTimer = null;
let currentMedia = null;
let seriesMetaVideos = [];
let userWatchlist = [];
let userHistory = [];
let activeTab = 'movies';
let configuredProviders = {};

const PROVIDER_META = {
  realdebrid: { name: 'Real-Debrid', short: 'RD', url: 'https://real-debrid.com/apitoken', label: 'real-debrid.com/apitoken', hint: 'Clé API Real-Debrid' },
  alldebrid:  { name: 'AllDebrid',   short: 'AD', url: 'https://alldebrid.fr/apikeys/',   label: 'alldebrid.fr/apikeys',   hint: 'Clé API AllDebrid' },
  torbox:     { name: 'TorBox',      short: 'TB', url: 'https://torbox.app/settings',     label: 'torbox.app/settings',     hint: 'Clé API TorBox' },
  debridlink: { name: 'Debrid-Link', short: 'DL', url: 'https://debrid-link.fr/webapp/apikey', label: 'debrid-link.fr/webapp/apikey', hint: 'Clé API Debrid-Link' },
  premiumize: { name: 'Premiumize',  short: 'PM', url: 'https://www.premiumize.me/account', label: 'premiumize.me/account', hint: 'Clé API Premiumize' },
  megadebrid: { name: 'Mega-Debrid', short: 'MD', url: 'https://www.mega-debrid.eu/index.php?page=account', label: 'mega-debrid.eu', hint: 'Token API ou identifiant:motdepasse Mega-Debrid' }
};

function onConfigProviderChange() {
  const prov = document.getElementById('cfgProvider') ? document.getElementById('cfgProvider').value : 'realdebrid';
  const meta = PROVIDER_META[prov] || PROVIDER_META.realdebrid;
  const linkEl = document.getElementById('cfgProviderLink');
  const lblEl = document.getElementById('cfgTokenLabel');
  const inpEl = document.getElementById('cfgToken');
  if (linkEl) {
    linkEl.href = meta.url;
    linkEl.textContent = meta.label;
  }
  const hasSaved = Boolean(configuredProviders && configuredProviders[prov]);
  if (lblEl) {
    lblEl.textContent = `${meta.hint}${hasSaved ? ' (✓ enregistrée)' : ''}`;
  }
  if (inpEl) {
    inpEl.placeholder = hasSaved
      ? `Laisser vide pour conserver votre clé ${meta.name}`
      : `Collez votre ${meta.hint.toLowerCase()}...`;
  }
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Erreur serveur');
  return data;
}

async function checkConfig() {
  try {
    const cfg = await api('/api/config');
    configuredProviders = cfg.configured_providers || {};
    const prov = cfg.debrid_provider || 'realdebrid';
    const pmeta = PROVIDER_META[prov] || PROVIDER_META.realdebrid;
    window.activeDebridProvider = prov;
    window.activeDebridName = pmeta.name;

    if (document.getElementById('cfgProvider')) {
      document.getElementById('cfgProvider').value = prov;
    }
    onConfigProviderChange();

    if (document.getElementById('logoProviderSub')) {
      document.getElementById('logoProviderSub').textContent = pmeta.name;
    }
    if (document.getElementById('tab-rdcloud')) {
      document.getElementById('tab-rdcloud').textContent = `Cloud ${pmeta.short}`;
    }
    if (document.getElementById('rdCloudHeading')) {
      document.getElementById('rdCloudHeading').textContent = `Derniers fichiers débridés sur ${pmeta.name}`;
    }

    document.getElementById('cfgDir').value = cfg.download_dir || '';
    if (document.getElementById('cfgPlayerMode')) {
      document.getElementById('cfgPlayerMode').value = cfg.player_mode || 'kino';
    }
    if (document.getElementById('cfgPrefLang')) {
      document.getElementById('cfgPrefLang').value = cfg.pref_lang || 'vf';
    }
    if (document.getElementById('cfgPrefQuality')) {
      document.getElementById('cfgPrefQuality').value = cfg.pref_quality || '4k';
    }
    if (document.getElementById('cfgHdrMode')) {
      const hMode = cfg.hdr_mode || 'sdr_pref';
      document.getElementById('cfgHdrMode').value = (hMode === 'hdr_native') ? 'hdr_native' : 'sdr_pref';
    }
    if (document.getElementById('cfgAudioMode')) {
      document.getElementById('cfgAudioMode').value = cfg.audio_mode || 'voice_boost';
    }
    window.kinoHdrMode = cfg.hdr_mode || 'sdr_pref';
    window.kinoAudioMode = cfg.audio_mode || 'voice_boost';
    const retVal = String(cfg.rd_retention_days || 0);
    if (document.getElementById('cfgRdRetention')) {
      document.getElementById('cfgRdRetention').value = retVal;
    }
    if (document.getElementById('rdCloudRetentionSelect')) {
      document.getElementById('rdCloudRetentionSelect').value = retVal;
    }
    window.kinoPlayerMode = cfg.player_mode || 'kino';
    const badge = document.getElementById('userBadge');
    if (badge) {
      if (cfg.user) {
        const exp = cfg.user.premium > 0 ? Math.ceil(cfg.user.premium / 86400) + 'j' : 'Gratuit';
        badge.innerHTML = `
          <span class="user-status-dot"></span>
          <span style="font-weight:600; color:#fafafa;">${pmeta.short}</span>
          <span style="color:var(--muted); opacity:0.8;">•</span>
          <span style="color:var(--text);">${escapeHtml(cfg.user.username)}</span>
          <span class="user-badge-exp">${exp}</span>
        `;
        badge.title = `Connecté à ${pmeta.name} — Compte actif (${exp} restants). Cliquer pour ouvrir la configuration.`;
      } else if (cfg.has_token) {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#ef4444; box-shadow:0 0 8px rgba(239,68,68,0.6);"></span>
          <span style="color:#ef4444; font-weight:600;">${pmeta.short} • Clé invalide</span>
        `;
        badge.title = 'Clé API invalide ou expirée. Cliquer pour reconfigurer.';
      } else {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#eab308; box-shadow:0 0 8px rgba(234,179,8,0.6);"></span>
          <span style="color:#eab308; font-weight:600;">Non configuré</span>
        `;
        badge.title = 'Cliquez pour configurer votre clé débrideur';
        openConfig();
      }
    }
  } catch (e) {
    console.error(e);
  }
}

function getInProgressHistory() {
  return (userHistory || []).filter(h => {
    if (h.imported_watched) return false;
    const pct = Number(h.progress_pct || 0);
    const isDone = Boolean(h.completed || pct >= 85);
    if (h.type === 'series') return true;
    return !isDone;
  });
}

function getWatchedHistory() {
  return (userHistory || []).filter(h => {
    const pct = Number(h.progress_pct || 0);
    return Boolean(h.completed || pct >= 85 || h.imported_watched);
  });
}

async function refreshUserLists() {
  try {
    const data = await api('/api/user-lists');
    userWatchlist = data.watchlist || [];
    userHistory = data.history || [];
    if (data.letterboxd_user) {
      window.savedLetterboxdUser = data.letterboxd_user;
    }
    await fetchCustomLists();
    updateListBadges();
    renderHomeResume();
  } catch (e) {
    console.warn(e);
  }
}

function isInWatchlist(id) {
  return userWatchlist.some(x => x.id === id);
}

async function toggleWatchlist(ev, media) {
  if (ev) ev.stopPropagation();
  const res = await api('/api/watchlist', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(media)
  });
  userWatchlist = res.watchlist || [];
  updateListBadges();
  if (activeTab === 'watchlist') {
    renderActiveListTab();
  } else {
    document.querySelectorAll(`[data-wl-id="${media.id}"]`).forEach(btn => {
      const inList = isInWatchlist(media.id);
      btn.classList.toggle('in-list', inList);
      btn.textContent = inList ? '✓' : '+';
      btn.title = inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste';
    });
  }
  const detailBtn = document.getElementById('detailWlBtn');
  if (detailBtn && currentMedia && currentMedia.id === media.id) {
    const inList = isInWatchlist(media.id);
    detailBtn.textContent = inList ? '✓ Dans ma liste' : '+ Ma Liste';
  }
}

function formatRemaining(pos, dur) {
  if (!pos || !dur || dur <= 30) return '';
  const remMin = Math.max(1, Math.round((dur - pos) / 60));
  if (remMin >= 60) {
    const h = Math.floor(remMin / 60);
    const m = remMin % 60;
    return `Il reste ${h}h${m > 0 ? String(m).padStart(2, '0') : ''}`;
  }
  return `Il reste ${remMin} min`;
}

let catalogSort = 'top';
let catalogSkip = 0;
let catalogItems = [];
let currentTrailerCtx = {trailerId: '', title: '', year: '', lang: 'vf'};

function getSeriesTargetEpisode(h) {
  if (!h || h.type !== 'series') return {season: 1, episode: 1, isNext: false};
  const s = Number(h.season || 1);
  const e = Number(h.episode || 1);
  const pct = Number(h.progress_pct || 0);
  const isDone = Boolean(h.completed || pct >= 85);
  if (isDone) {
    return {
      season: Number(h.next_season || s),
      episode: Number(h.next_episode || (e + 1)),
      isNext: true,
      prevSeason: s,
      prevEpisode: e
    };
  }
  return {
    season: s,
    episode: e,
    isNext: false,
    nextSeason: Number(h.next_season || s),
    nextEpisode: Number(h.next_episode || (e + 1))
  };
}

function renderHomeResume() {
  const sec = document.getElementById('homeResumeSection');
  const grid = document.getElementById('homeResumeGrid');
  const inProg = getInProgressHistory();
  if (activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'history') {
    sec.style.display = 'none';
    return;
  }
  const items = activeTab === 'history' ? inProg : inProg.slice(0, 4);
  if (!items.length) {
    if (activeTab === 'history') {
      sec.style.display = 'block';
      grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem; grid-column:1/-1;">Aucune lecture en cours pour le moment. Vos films terminés se trouvent dans l\'onglet <strong>Déjà vus</strong>.</p>';
    } else {
      sec.style.display = 'none';
    }
    return;
  }
  sec.style.display = 'block';
  grid.innerHTML = items.map(h => {
    const isSeries = h.type === 'series';
    const epTag = isSeries && h.season && h.episode
      ? `S${String(h.season).padStart(2,'0')}E${String(h.episode).padStart(2,'0')}`
      : '';
    const nextS = isSeries ? Number(h.next_season || h.season || 1) : null;
    const nextEp = isSeries && h.episode ? Number(h.next_episode || (Number(h.episode) + 1)) : null;
    const nextTag = isSeries && nextS && nextEp
      ? `S${String(nextS).padStart(2,'0')}E${String(nextEp).padStart(2,'0')}`
      : '';
    const pct = Number(h.progress_pct || 0);
    const isEpDone = Boolean(h.completed || pct >= 85);
    const remTxt = (pct > 0 && !isEpDone)
      ? formatRemaining(h.position, h.duration)
      : (isEpDone ? (isSeries ? `✓ ${epTag} terminé · Prêt : ${nextTag}` : '✓ Terminé') : '');
    const subInfo = [(!isEpDone ? epTag : '') || h.year || 'En cours', remTxt].filter(Boolean).join(' • ');
    const progBar = pct > 0 ? `
      <div style="height:3px; background:var(--surface-2); border-radius:2px; overflow:hidden; margin-top:6px;">
        <div style="height:100%; width:${Math.min(100, pct)}%; background:var(--text);"></div>
      </div>
    ` : '';
    const mediaPayload = JSON.stringify({id: h.id, name: h.name, type: h.type || 'movie', year: h.year || '', poster: h.poster || ''}).replace(/'/g, "&#39;");
    const resumePayload = JSON.stringify({
      imdb_id: h.id,
      type: h.type || 'movie',
      season: h.season || 1,
      episode: h.episode || 1,
      title: isSeries ? `${h.name} — ${epTag}` : h.name,
      name: h.name,
      poster: h.poster || '',
      year: h.year || ''
    }).replace(/'/g, "&#39;");
    const nextPayload = isSeries ? JSON.stringify({
      imdb_id: h.id,
      type: 'series',
      season: nextS || 1,
      episode: nextEp || 1,
      title: `${h.name} — ${nextTag}`,
      name: h.name,
      poster: h.poster || '',
      year: h.year || ''
    }).replace(/'/g, "&#39;") : '';

    const buttonsHtml = (isSeries && isEpDone)
      ? `
        <button class="btn" style="padding:5px 10px; font-size:0.75rem;" onclick='oneClickPlay(${nextPayload}, this)'>▶ Épisode suivant (${nextTag})</button>
        <button class="btn btn-secondary" style="padding:5px 9px; font-size:0.75rem;" onclick='oneClickPlay(${resumePayload}, this)'>Revoir ${epTag}</button>
      `
      : `
        <button class="btn" style="padding:5px 10px; font-size:0.75rem;" onclick='oneClickPlay(${resumePayload}, this)'>Reprendre ${epTag}</button>
        ${isSeries ? `<button class="btn btn-secondary" style="padding:5px 9px; font-size:0.75rem;" onclick='oneClickPlay(${nextPayload}, this)'>Suivant (${nextTag})</button>` : ''}
      `;

    return `
      <div class="resume-card">
        <img src="${h.poster || ''}" alt="" onerror="this.style.opacity=0.08" onclick='selectMedia(${mediaPayload})' style="cursor:pointer;">
        <div style="flex:1; min-width:0;">
          <div style="font-weight:500; font-size:0.86rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; cursor:pointer;" onclick='selectMedia(${mediaPayload})'>${h.name}</div>
          <div style="color:var(--dim); font-size:0.75rem; margin-top:2px;">${subInfo}</div>
          ${progBar}
          <div style="display:flex; gap:6px; margin-top:8px; flex-wrap:wrap;">
            ${buttonsHtml}
            <button class="btn btn-secondary" style="padding:5px 8px; font-size:0.74rem;" onclick='removeHistoryItem(${JSON.stringify(h.id)})' title="Retirer">×</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function removeHistoryItem(id) {
  await api('/api/history-remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  });
  await refreshUserLists();
}

async function clearAllHistory() {
  await removeHistoryItem('__all__');
}

let classicsCatalogCache = [];

function updateClassicsStatsBadge() {
  const statsEl = document.getElementById('listStatsBadge');
  if (!statsEl) return;
  if (activeTab !== 'classics') return;
  const pool = catalogItems && catalogItems.length ? catalogItems : classicsCatalogCache;
  if (!pool.length) {
    statsEl.style.display = 'none';
    return;
  }
  const seenCount = pool.filter(m => {
    const h = userHistory.find(x => x.id === m.id);
    return Boolean(h && (h.completed || Number(h.progress_pct || 0) >= 85));
  }).length;
  const pct = Math.round((seenCount / pool.length) * 100);
  const rem = pool.length - seenCount;
  statsEl.textContent = `${seenCount} / ${pool.length} vus (${pct}%) • ${rem} à découvrir`;
  statsEl.style.display = 'inline-flex';
}

function renderGenreChipsForTab(tab) {
  const gf = document.getElementById('genreFilters');
  if (!gf) return;
  if (tab === 'anime') {
    gf.innerHTML = `
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Tous</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Films d'Animation" onclick="selectGenre('Films d\\'Animation', this)">Films d'Animation</span>
    `;
  } else {
    gf.innerHTML = `
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Tous</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Thriller" onclick="selectGenre('Thriller', this)">Thriller</span>
      <span class="chip" data-genre="Crime" onclick="selectGenre('Crime', this)">Policier</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Animation" onclick="selectGenre('Animation', this)">Animation</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Horror" onclick="selectGenre('Horror', this)">Horreur</span>
      <span class="chip" data-genre="War" onclick="selectGenre('War', this)">Guerre</span>
      <span class="chip" data-genre="Western" onclick="selectGenre('Western', this)">Western</span>
      <span class="chip" data-genre="Documentary" onclick="selectGenre('Documentary', this)">Documentaire</span>
    `;
  }
}

function updateCatalogHeading() {
  const activeChip = document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`);
  const genreLabel = (activeGenre && activeChip) ? activeChip.textContent : '';
  if (activeTab === 'classics') {
    const sortSuffix = catalogSort === 'imdbRating'
      ? ' — ★ Mieux notés IMDb'
      : (catalogSort === 'recent' ? ' — Plus récents' : (catalogSort === 'oldest' ? ' — Chronologique' : ' · Les films à voir dans sa vie'));
    document.getElementById('catalogTitle').textContent = genreLabel
      ? `Classiques · ${genreLabel}`
      : `Classiques${sortSuffix}`;
    return;
  }
  if (activeTab === 'anime') {
    const sortSuffix = catalogSort === 'imdbRating'
      ? ' · Mieux notés (★ IMDb)'
      : (catalogSort === 'recent' ? ' · Plus récents' : (catalogSort === 'oldest' ? ' · Chronologique' : ''));
    document.getElementById('catalogTitle').textContent = genreLabel
      ? `Animation Japonaise · ${genreLabel}`
      : `Animation Japonaise${sortSuffix}`;
    return;
  }
  const type = activeTab === 'series' ? 'series' : 'movie';
  const baseLabel = type === 'series' ? 'Séries' : 'Films';
  const sortSuffix = catalogSort === 'imdbRating'
    ? ' — ★ Mieux notés IMDb'
    : (catalogSort === 'recent' ? ' — Plus récents' : (catalogSort === 'oldest' ? ' — Chronologique' : ' populaires du moment'));
  document.getElementById('catalogTitle').textContent = genreLabel
    ? `${baseLabel} · ${genreLabel}${catalogSort === 'imdbRating' ? ' (★ IMDb)' : ''}`
    : `${baseLabel}${sortSuffix}`;
}

function selectGenre(genre, el) {
  activeGenre = genre;
  document.querySelectorAll('#genreFilters .chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  updateCatalogHeading();
  loadCatalog(type, genre, true);
}

function onChangeCatalogSort(sortVal) {
  catalogSort = sortVal || 'top';
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  updateCatalogHeading();
  loadCatalog(type, activeGenre, true);
}

let heroSpotlightItems = [];
let heroSpotlightIdx = 0;
let heroSpotlightTimer = null;

function renderHeroSpotlight(metas, fallbackType) {
  const box = document.getElementById('heroSpotlight');
  if (!box) return;
  clearInterval(heroSpotlightTimer);
  if (!metas || !metas.length || (activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'classics' && activeTab !== 'anime')) {
    box.style.display = 'none';
    return;
  }
  const unwatched = metas.filter(m => {
    const h = userHistory.find(x => x.id === m.id);
    if (!h) return true;
    return !(h.completed || Number(h.progress_pct || 0) >= 85);
  });
  heroSpotlightItems = (unwatched.length >= 3 ? unwatched : metas).slice(0, 5).map(m => ({
    ...m,
    type: (m.type && m.type !== 'classics') ? m.type : (fallbackType === 'series' ? 'series' : 'movie')
  }));
  heroSpotlightIdx = 0;
  drawHeroSpotlightSlide();
  box.style.display = 'flex';
  heroSpotlightTimer = setInterval(() => {
    if (document.getElementById('inAppPlayerOverlay').classList.contains('active')) return;
    heroSpotlightIdx = (heroSpotlightIdx + 1) % heroSpotlightItems.length;
    drawHeroSpotlightSlide();
  }, 9000);
}

function setHeroSpotlightIdx(idx) {
  clearInterval(heroSpotlightTimer);
  heroSpotlightIdx = ((idx % heroSpotlightItems.length) + heroSpotlightItems.length) % heroSpotlightItems.length;
  drawHeroSpotlightSlide();
}

function drawHeroSpotlightSlide() {
  const box = document.getElementById('heroSpotlight');
  if (!box || !heroSpotlightItems.length) return;
  const item = heroSpotlightItems[heroSpotlightIdx] || heroSpotlightItems[0];
  const mtype = item.type || 'movie';
  const year = String(item.releaseInfo || item.year || '');
  const rating = item.imdbRating || '';
  const bgUrl = item.background || `https://images.metahub.space/background/medium/${item.id}/img`;
  const genres = Array.isArray(item.genres) ? item.genres.slice(0, 3) : [];
  const desc = item.description || '';
  const mediaObj = {
    id: item.id,
    name: item.name,
    type: mtype,
    year,
    poster: item.poster || '',
    imdbRating: String(rating)
  };
  const payload = JSON.stringify(mediaObj).replace(/'/g, "&#39;");
  const safeNameJs = JSON.stringify(item.name || 'KINO').replace(/'/g, "&#39;");
  const safeYearJs = JSON.stringify(year).replace(/'/g, "&#39;");
  const kickerTxt = activeTab === 'classics'
    ? `★ PANTHÉON DU CINÉMA · CLASSIQUE INCONTOURNABLE #${heroSpotlightIdx + 1}`
    : (activeTab === 'anime'
        ? `★ ANIMATION JAPONAISE · POPULAIRE #${heroSpotlightIdx + 1}`
        : `★ À LA UNE · ${mtype === 'series' ? 'SÉRIE' : 'FILM'} #${heroSpotlightIdx + 1}`);

  box.style.backgroundImage = `url('${bgUrl}')`;
  box.innerHTML = `
    <div class="hero-content">
      <div class="hero-kicker">${kickerTxt}</div>
      <div class="hero-title">${item.name}</div>
      <div class="hero-meta">
        ${year ? `<span>${year}</span>` : ''}
        ${rating ? `<span style="color:#fafafa; font-weight:600;">★ ${rating} IMDb</span>` : ''}
        ${genres.map(g => `<span class="badge">${g}</span>`).join('')}
      </div>
      ${desc ? `<div class="hero-desc">${desc}</div>` : ''}
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:6px;">
        <button class="btn" onclick='oneClickCard(event, this, ${payload})'>Play</button>
        <button class="btn btn-secondary" onclick='selectMedia(${payload})'>Fiche &amp; Sources</button>
        <button class="btn btn-secondary" onclick='openTrailerModal("", ${safeNameJs}, ${safeYearJs}, "vf")'><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>Bande-annonce</button>
        <button class="btn-surprise" onclick="surpriseMeMedia()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="4" width="20" height="16" rx="3"/>
            <line x1="12" y1="4" x2="12" y2="20"/>
          </svg>
          <span>Surprends-moi</span>
        </button>
      </div>
    </div>
    <div class="hero-dots">
      ${heroSpotlightItems.map((_, i) => `<button class="hero-dot ${i === heroSpotlightIdx ? 'active' : ''}" onclick="setHeroSpotlightIdx(${i})" title="Titre ${i + 1}"></button>`).join('')}
    </div>
  `;
}

/* ============================================================================
 * ROULETTE HORIZONTALE KINO (SÉLECTION ALÉATOIRE)
 * ============================================================================ */
let caseAudioCtx = null;
let caseSoundEnabled = true;
let caseSpinRaf = null;
let caseSpinState = null;
let caseNoiseBuffer = null;

function ensureCaseAudio() {
  if (!caseSoundEnabled) return null;
  try {
    if (!caseAudioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) caseAudioCtx = new Ctx();
    }
    if (caseAudioCtx && caseAudioCtx.state === 'suspended') {
      caseAudioCtx.resume();
    }
    if (caseAudioCtx && !caseNoiseBuffer) {
      const sr = caseAudioCtx.sampleRate;
      const len = Math.floor(sr * 0.08);
      caseNoiseBuffer = caseAudioCtx.createBuffer(1, len, sr);
      const data = caseNoiseBuffer.getChannelData(0);
      for (let i = 0; i < len; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (sr * 0.014));
      }
    }
    return caseAudioCtx;
  } catch (e) {
    return null;
  }
}

function playCaseLaunchSound() {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // 1. Impact grave d'ouverture (Sub-Thump)
    const subOsc = ctx.createOscillator();
    const subGain = ctx.createGain();
    subOsc.type = 'sine';
    subOsc.frequency.setValueAtTime(135, now);
    subOsc.frequency.exponentialRampToValueAtTime(38, now + 0.18);
    subGain.gain.setValueAtTime(0.24, now);
    subGain.gain.exponentialRampToValueAtTime(0.001, now + 0.20);
    subOsc.connect(subGain);
    subGain.connect(ctx.destination);
    subOsc.start(now);
    subOsc.stop(now + 0.21);

    // 2. Déclic mécanique métallique
    if (caseNoiseBuffer) {
      const noise = ctx.createBufferSource();
      noise.buffer = caseNoiseBuffer;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.setValueAtTime(2400, now + 0.02);
      filter.Q.setValueAtTime(3.2, now + 0.02);
      const nGain = ctx.createGain();
      nGain.gain.setValueAtTime(0.18, now + 0.02);
      nGain.gain.exponentialRampToValueAtTime(0.001, now + 0.065);
      noise.connect(filter);
      filter.connect(nGain);
      nGain.connect(ctx.destination);
      noise.start(now + 0.02);
    }

    // 3. Sweep harmonique ascendant (mise sous tension de la roue)
    const sweepOsc = ctx.createOscillator();
    const sweepGain = ctx.createGain();
    sweepOsc.type = 'triangle';
    sweepOsc.frequency.setValueAtTime(196, now + 0.03);
    sweepOsc.frequency.exponentialRampToValueAtTime(587.33, now + 0.26);
    sweepGain.gain.setValueAtTime(0.001, now + 0.03);
    sweepGain.gain.linearRampToValueAtTime(0.11, now + 0.09);
    sweepGain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
    sweepOsc.connect(sweepGain);
    sweepGain.connect(ctx.destination);
    sweepOsc.start(now + 0.03);
    sweepOsc.stop(now + 0.29);
  } catch (e) {}
}

function playCaseTickSound(progress = 0, imdbRating = 0) {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // Couche 1 : Clac physique du cran (bruit filtré court + boisé grave)
    if (caseNoiseBuffer) {
      const noise = ctx.createBufferSource();
      noise.buffer = caseNoiseBuffer;
      const bp = ctx.createBiquadFilter();
      bp.type = 'bandpass';
      bp.frequency.setValueAtTime(1950 - (progress * 450), now);
      bp.Q.setValueAtTime(4.0, now);
      const nGain = ctx.createGain();
      const clickVol = 0.14 + (progress * 0.08);
      nGain.gain.setValueAtTime(clickVol, now);
      nGain.gain.exponentialRampToValueAtTime(0.001, now + 0.022);
      noise.connect(bp);
      bp.connect(nGain);
      nGain.connect(ctx.destination);
      noise.start(now);
    }

    // Corps percussif boisé/carbone du cran
    const bodyOsc = ctx.createOscillator();
    const bodyGain = ctx.createGain();
    bodyOsc.type = 'triangle';
    const bodyStart = 310 - (progress * 70);
    bodyOsc.frequency.setValueAtTime(bodyStart, now);
    bodyOsc.frequency.exponentialRampToValueAtTime(68, now + 0.030);
    bodyGain.gain.setValueAtTime(0.16 + (progress * 0.06), now);
    bodyGain.gain.exponentialRampToValueAtTime(0.001, now + 0.034);
    bodyOsc.connect(bodyGain);
    bodyGain.connect(ctx.destination);
    bodyOsc.start(now);
    bodyOsc.stop(now + 0.036);

    // Couche 2 : Note cristalline de tension qui monte subtilement
    const pingOsc = ctx.createOscillator();
    const pingGain = ctx.createGain();
    pingOsc.type = 'sine';
    const ratingVal = parseFloat(imdbRating || '0') || 0;
    const ratingBoost = ratingVal >= 8.5 ? 120 : (ratingVal >= 8.0 ? 60 : 0);
    const pingFreq = 380 + (progress * 260) + ratingBoost;
    pingOsc.frequency.setValueAtTime(pingFreq, now);
    pingOsc.frequency.exponentialRampToValueAtTime(pingFreq * 0.96, now + 0.045);
    const pingDur = progress > 0.65 ? 0.075 : 0.042;
    pingGain.gain.setValueAtTime(0.068, now);
    pingGain.gain.exponentialRampToValueAtTime(0.0008, now + pingDur);
    pingOsc.connect(pingGain);
    pingGain.connect(ctx.destination);
    pingOsc.start(now);
    pingOsc.stop(now + pingDur + 0.005);

    // Couche 3 : Pulsation sub-bass dramatique sur les derniers crans au ralenti
    if (progress > 0.66) {
      const sub = ctx.createOscillator();
      const sGain = ctx.createGain();
      sub.type = 'sine';
      sub.frequency.setValueAtTime(95, now);
      sub.frequency.exponentialRampToValueAtTime(42, now + 0.07);
      sGain.gain.setValueAtTime(0.15 * ((progress - 0.66) / 0.34), now);
      sGain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
      sub.connect(sGain);
      sGain.connect(ctx.destination);
      sub.start(now);
      sub.stop(now + 0.085);
    }
  } catch (e) {}
}

function playCaseWinSound(imdbRating = 0) {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // 1. Sub-drop d'impact de verrouillage
    const dropOsc = ctx.createOscillator();
    const dropGain = ctx.createGain();
    dropOsc.type = 'sine';
    dropOsc.frequency.setValueAtTime(140, now);
    dropOsc.frequency.exponentialRampToValueAtTime(36, now + 0.38);
    dropGain.gain.setValueAtTime(0.28, now);
    dropGain.gain.exponentialRampToValueAtTime(0.001, now + 0.42);
    dropOsc.connect(dropGain);
    dropGain.connect(ctx.destination);
    dropOsc.start(now);
    dropOsc.stop(now + 0.44);

    // 2. Accord arpégé de révélation
    const rVal = parseFloat(imdbRating || '0') || 0;
    const notes = rVal >= 8.2
      ? [523.25, 659.25, 783.99, 987.77, 1174.66]
      : [440.0, 554.37, 659.25, 880.0];
    notes.forEach((freq, idx) => {
      const delay = idx * 0.048;
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();
      osc1.type = 'triangle';
      osc2.type = 'sine';
      osc1.frequency.setValueAtTime(freq, now + delay);
      osc2.frequency.setValueAtTime(freq * 1.003, now + delay);
      gain.gain.setValueAtTime(0.001, now + delay);
      gain.gain.linearRampToValueAtTime(0.11, now + delay + 0.018);
      gain.gain.exponentialRampToValueAtTime(0.0006, now + delay + 0.72);
      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);
      osc1.start(now + delay);
      osc2.start(now + delay);
      osc1.stop(now + delay + 0.75);
      osc2.stop(now + delay + 0.75);
    });
  } catch (e) {}
}

function toggleCaseSound() {
  caseSoundEnabled = !caseSoundEnabled;
  const btn = document.getElementById('caseSoundBtn');
  if (btn) {
    btn.textContent = caseSoundEnabled ? 'Son : Activé' : 'Son : Muet';
  }
}

function closeCaseModal() {
  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }
  caseSpinState = null;
  const modal = document.getElementById('caseModal');
  if (modal) {
    modal.style.display = 'none';
    modal.classList.remove('active');
  }
}

let activeCasePoolMode = 'catalog';
let hideWatchedInCatalog = false;

function toggleHideWatchedCatalog() {
  hideWatchedInCatalog = !hideWatchedInCatalog;
  const chip = document.getElementById('hideWatchedChip');
  if (chip) {
    chip.classList.toggle('active', hideWatchedInCatalog);
  }
  if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
    renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
  }
}

function surpriseMeFromCurrentList() {
  if (activeTab === 'watched') {
    surpriseMeMedia('watched');
  } else {
    surpriseMeMedia('watchlist');
  }
}

function renderActiveListTab() {
  if (activeTab !== 'watchlist' && activeTab !== 'watched') return;
  const rawList = activeTab === 'watchlist' ? (userWatchlist || []) : getWatchedHistory();
  const q = (document.getElementById('listFilterInput')?.value || '').trim().toLowerCase();
  const sortMode = document.getElementById('listSortSelect')?.value || 'added';
  const statsEl = document.getElementById('listStatsBadge');

  // Calcul des statistiques de la liste
  if (statsEl) {
    if (rawList.length > 0) {
      const rated = rawList.map(x => parseFloat(x.imdbRating || '0')).filter(r => r > 0);
      const avgTxt = rated.length ? ` • ★ ${(rated.reduce((a, b) => a + b, 0) / rated.length).toFixed(1)} moy.` : '';
      if (activeTab === 'watchlist') {
        const unwatchedCount = rawList.filter(m => {
          const h = userHistory.find(x => x.id === m.id);
          return !(h && (h.completed || Number(h.progress_pct || 0) >= 85));
        }).length;
        statsEl.textContent = `${rawList.length} titre${rawList.length > 1 ? 's' : ''} (${unwatchedCount} à voir)${avgTxt}`;
      } else {
        statsEl.textContent = `${rawList.length} film${rawList.length > 1 ? 's' : ''} vu${rawList.length > 1 ? 's' : ''}${avgTxt}`;
      }
      statsEl.style.display = 'inline-flex';
    } else {
      statsEl.style.display = 'none';
    }
  }

  let filtered = rawList.filter(m => {
    if (!q) return true;
    const hay = `${m.name || ''} ${m.year || m.releaseInfo || ''}`.toLowerCase();
    return hay.includes(q);
  });

  if (sortMode === 'rating') {
    filtered = [...filtered].sort((a, b) => (parseFloat(b.imdbRating || '0') || 0) - (parseFloat(a.imdbRating || '0') || 0));
  } else if (sortMode === 'year_desc') {
    filtered = [...filtered].sort((a, b) => (parseInt(b.year || b.releaseInfo || '0', 10) || 0) - (parseInt(a.year || a.releaseInfo || '0', 10) || 0));
  } else if (sortMode === 'year_asc') {
    filtered = [...filtered].sort((a, b) => (parseInt(a.year || a.releaseInfo || '9999', 10) || 9999) - (parseInt(b.year || b.releaseInfo || '9999', 10) || 9999));
  } else if (sortMode === 'alpha') {
    filtered = [...filtered].sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'fr'));
  }

  renderPosterCards(filtered, 'movie');
}

async function surpriseMeMedia(poolArg = null) {
  let poolMode = 'catalog';
  let customListItems = null;

  if (typeof poolArg === 'object' && poolArg && poolArg.items) {
    poolMode = 'customlist';
    customListItems = poolArg.items;
    window._customListRouletteTitle = poolArg.title || 'Liste Letterboxd';
  } else if (typeof poolArg === 'string' && poolArg.startsWith('customlist:')) {
    poolMode = 'customlist';
    const lid = poolArg.split(':')[1];
    const l = userCustomLists.find(x => x.id === lid);
    customListItems = l?.items || [];
    window._customListRouletteTitle = l?.title || 'Liste Letterboxd';
  } else if (poolArg === true || poolArg === 'watchlist') {
    poolMode = 'watchlist';
  } else if (poolArg === 'watched' || poolArg === 'classics' || poolArg === 'prestige' || poolArg === 'catalog') {
    poolMode = poolArg === 'prestige' ? 'classics' : poolArg;
  } else if (activeTab === 'watchlist') {
    poolMode = 'watchlist';
  } else if (activeTab === 'watched') {
    poolMode = 'watched';
  } else if (activeTab === 'classics') {
    poolMode = 'classics';
  }
  activeCasePoolMode = poolMode;

  document.querySelectorAll('#casePoolChips .chip').forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-pool') === poolMode);
  });

  let sourceItems = [];
  if (poolMode === 'customlist') {
    sourceItems = customListItems || [];
  } else if (poolMode === 'watchlist') {
    sourceItems = (userWatchlist && userWatchlist.length) ? userWatchlist : catalogItems;
  } else if (poolMode === 'watched') {
    const wList = getWatchedHistory();
    sourceItems = wList.length ? wList : catalogItems;
  } else if (poolMode === 'classics') {
    if (activeTab === 'classics' && catalogItems && catalogItems.length) {
      sourceItems = catalogItems;
    } else {
      if (!classicsCatalogCache || !classicsCatalogCache.length) {
        try {
          const data = await api('/api/catalog?type=classics&sort=top');
          classicsCatalogCache = data.metas || [];
        } catch (e) {}
      }
      sourceItems = (classicsCatalogCache && classicsCatalogCache.length) ? classicsCatalogCache : catalogItems;
    }
  } else {
    sourceItems = catalogItems;
  }

  if (!sourceItems || !sourceItems.length) return;
  const fallbackType = (activeTab === 'series' || activeTab === 'anime') ? 'series' : 'movie';

  const modal = document.getElementById('caseModal');
  const strip = document.getElementById('caseRollerStrip');
  const viewport = document.getElementById('caseRollerViewport');
  const winPanel = document.getElementById('caseWinnerPanel');
  const skipBtn = document.getElementById('caseSkipBtn');
  const titleEl = document.getElementById('caseModalTitle');
  const kickerEl = document.getElementById('caseModalKicker');
  if (!modal || !strip || !viewport) return;

  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }

  // Son d'ouverture au clic
  playCaseLaunchSound();

  // Filtrer les œuvres déjà vues (sauf si on est volontairement en mode Rewatch 'watched')
  let rawPool = sourceItems;
  if (poolMode !== 'watched') {
    const unwatchedItems = sourceItems.filter(m => {
      const h = userHistory.find(x => x.id === m.id);
      const isWatched = Boolean(h && (h.completed || Number(h.progress_pct || 0) >= 85));
      return !isWatched;
    });
    if (unwatchedItems.length) rawPool = unwatchedItems;
  }

  // Dédoublonner le pool par identifiant ou titre pour éviter les doublons
  const getItemKey = (item) => String((item && (item.id || item.name)) || '').trim().toLowerCase();
  const seenPoolKeys = new Set();
  const stripPool = [];
  for (const item of rawPool) {
    const k = getItemKey(item);
    if (!k || seenPoolKeys.has(k)) continue;
    seenPoolKeys.add(k);
    stripPool.push(item);
  }
  if (!stripPool.length) return;

  // Filtrer le lot gagnant selon la source choisie
  let winnerPool = stripPool;
  if (poolMode === 'catalog') {
    const goodRated = stripPool.filter(m => (parseFloat(m.imdbRating || '0') || 0) >= 7.4);
    if (goodRated.length) winnerPool = goodRated;
  }

  const winnerRaw = winnerPool[Math.floor(Math.random() * winnerPool.length)];
  if (!winnerRaw) return;
  const winnerKey = getItemKey(winnerRaw);

  // Construire un ruban de 50 cartes sans jamais avoir 2 films identiques de suite (ni autour du gagnant)
  const TOTAL_SLOTS = 50;
  const WINNER_INDEX = 40;
  const slots = [];
  const windowSize = Math.min(4, Math.max(1, stripPool.length - 1));
  const winnerGuardRadius = Math.min(3, Math.max(1, stripPool.length - 1));

  function makeShuffledDeck(arr) {
    const copy = [...arr];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  let deck = makeShuffledDeck(stripPool);

  for (let i = 0; i < TOTAL_SLOTS; i++) {
    if (i === WINNER_INDEX) {
      slots.push(winnerRaw);
      deck = deck.filter(x => getItemKey(x) !== winnerKey);
      continue;
    }

    const forbidden = new Set();
    for (let back = 1; back <= windowSize; back++) {
      if (i - back >= 0 && slots[i - back]) {
        forbidden.add(getItemKey(slots[i - back]));
      }
    }
    if (Math.abs(i - WINNER_INDEX) <= winnerGuardRadius && stripPool.length > 1) {
      forbidden.add(winnerKey);
    }

    let pickIdx = deck.findIndex(x => !forbidden.has(getItemKey(x)));
    if (pickIdx === -1) {
      deck = makeShuffledDeck(stripPool);
      pickIdx = deck.findIndex(x => !forbidden.has(getItemKey(x)));
    }
    if (pickIdx === -1 && i > 0 && stripPool.length > 1) {
      const prevKey = getItemKey(slots[i - 1]);
      pickIdx = deck.findIndex(x => getItemKey(x) !== prevKey);
    }
    const chosen = pickIdx !== -1 ? deck.splice(pickIdx, 1)[0] : deck.shift() || stripPool[0];
    slots.push(chosen);
  }

  strip.innerHTML = slots.map((m, idx) => {
    const ratingVal = parseFloat(m.imdbRating || '0') || 0;
    const ratingBadge = ratingVal > 0 ? `<span class="case-slot-rating">★ ${m.imdbRating}</span>` : '';
    const yearTxt = m.releaseInfo || m.year || (m.type === 'series' ? 'Série' : 'Film');
    const poster = m.poster || '';
    const safeName = String(m.name || 'Titre').replace(/"/g, '&quot;');
    return `
      <div class="case-slot-card" id="caseSlot_${idx}">
        ${ratingBadge}
        ${poster
          ? `<img class="case-slot-poster" src="${poster}" alt="${safeName}" loading="eager" onerror="this.style.visibility='hidden'">`
          : `<div class="case-slot-poster" style="display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:0.75rem;">KINO</div>`
        }
        <div class="case-slot-info">
          <div class="case-slot-title" title="${safeName}">${m.name || 'Sans titre'}</div>
          <div class="case-slot-sub">${yearTxt}</div>
        </div>
      </div>
    `;
  }).join('');

  winPanel.style.display = 'none';
  winPanel.innerHTML = '';
  if (skipBtn) skipBtn.style.display = 'inline-flex';
  if (kickerEl) {
    if (poolMode === 'watchlist') kickerEl.textContent = 'TIRAGE · MA LISTE';
    else if (poolMode === 'watched') kickerEl.textContent = 'TIRAGE · DÉJÀ VUS';
    else if (poolMode === 'classics') kickerEl.textContent = 'TIRAGE · CLASSIQUES À VOIR DANS SA VIE';
    else if (poolMode === 'customlist') kickerEl.textContent = `TIRAGE · ${(window._customListRouletteTitle || 'LISTE LETTERBOXD').toUpperCase()}`;
    else kickerEl.textContent = activeGenre ? `TIRAGE · ${activeGenre.toUpperCase()}` : (fallbackType === 'series' ? 'TIRAGE · SÉRIES' : 'TIRAGE · FILMS');
  }
  if (titleEl) titleEl.textContent = 'Sélection en cours...';

  modal.style.display = 'flex';
  modal.classList.add('active');

  // Dimensions géométriques du ruban
  const CARD_W = 152;
  const GAP = 10;
  const STEP = CARD_W + GAP; // 162px par carte
  const PAD_LEFT = 12;
  const vpWidth = viewport.clientWidth || 880;
  const centerOffset = vpWidth / 2;

  // Position exacte du centre de la carte gagnante (index 40)
  const winnerCenterPx = PAD_LEFT + (WINNER_INDEX * STEP) + (CARD_W / 2);
  // Léger décalage aléatoire à l'intérieur de la carte pour garder le suspense sur la décélération
  const jitterPx = (Math.random() - 0.5) * (CARD_W * 0.72);
  const startTranslate = 0;
  const targetTranslate = -(winnerCenterPx + jitterPx - centerOffset);
  const exactCenteredTranslate = -(winnerCenterPx - centerOffset);

  strip.style.transition = 'none';
  strip.style.transform = `translate3d(${startTranslate}px, 0, 0)`;

  const DURATION = 5100;
  const startTime = performance.now();
  let lastTickSlot = 0;

  caseSpinState = {
    winnerRaw,
    fallbackType,
    targetTranslate,
    exactCenteredTranslate,
    winnerIdx: WINNER_INDEX,
    poolMode,
    finished: false
  };

  function stepSpin(now) {
    if (!caseSpinState || caseSpinState.finished) return;
    const elapsed = Math.min(DURATION, now - startTime);
    const t = elapsed / DURATION;
    const ease = 1 - Math.pow(1 - t, 4.7);
    const currentX = startTranslate + (targetTranslate - startTranslate) * ease;
    strip.style.transform = `translate3d(${currentX.toFixed(2)}px, 0, 0)`;

    const distUnderLaser = Math.abs(currentX) + centerOffset - PAD_LEFT;
    const currentSlot = Math.floor(distUnderLaser / STEP);
    if (currentSlot !== lastTickSlot && currentSlot >= 0 && currentSlot < TOTAL_SLOTS) {
      lastTickSlot = currentSlot;
      playCaseTickSound(t, slots[currentSlot]?.imdbRating);
    }

    if (t < 1) {
      caseSpinRaf = requestAnimationFrame(stepSpin);
    } else {
      finishCaseSpin();
    }
  }

  caseSpinRaf = requestAnimationFrame(stepSpin);
}

function skipCaseSpin() {
  if (!caseSpinState || caseSpinState.finished) return;
  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }
  finishCaseSpin(true);
}

function finishCaseSpin(skipped = false) {
  if (!caseSpinState || caseSpinState.finished) return;
  caseSpinState.finished = true;
  const { winnerRaw, fallbackType, exactCenteredTranslate, winnerIdx, poolMode } = caseSpinState;

  const strip = document.getElementById('caseRollerStrip');
  const winCard = document.getElementById(`caseSlot_${winnerIdx}`);
  const winPanel = document.getElementById('caseWinnerPanel');
  const skipBtn = document.getElementById('caseSkipBtn');
  const titleEl = document.getElementById('caseModalTitle');

  if (skipBtn) skipBtn.style.display = 'none';

  // Recentrage magnétique doux sur le film sélectionné
  if (strip) {
    strip.style.transition = skipped ? 'transform 0.18s ease-out' : 'transform 0.38s cubic-bezier(0.22, 1, 0.36, 1)';
    strip.style.transform = `translate3d(${exactCenteredTranslate.toFixed(2)}px, 0, 0)`;
  }
  if (winCard) {
    winCard.classList.add('winner-locked');
  }

  playCaseWinSound(winnerRaw.imdbRating);

  const mtype = winnerRaw.type || fallbackType || 'movie';
  const year = String(winnerRaw.releaseInfo || winnerRaw.year || '');
  const rating = String(winnerRaw.imdbRating || '');
  const genres = Array.isArray(winnerRaw.genres) ? winnerRaw.genres.slice(0, 3) : [];
  const inWl = isInWatchlist(winnerRaw.id);

  if (titleEl) {
    titleEl.textContent = winnerRaw.name || 'Sélection aléatoire';
  }

  const mediaObj = {
    id: winnerRaw.id,
    name: winnerRaw.name,
    type: mtype,
    year,
    poster: winnerRaw.poster || '',
    imdbRating: rating,
    poolMode: poolMode || 'catalog'
  };
  window._lastCaseWinnerMedia = mediaObj;

  if (winPanel) {
    winPanel.style.borderColor = '';
    winPanel.style.boxShadow = '';
    winPanel.innerHTML = `
      <div style="display:flex; align-items:center; gap:14px; min-width:240px; flex:1;">
        ${winnerRaw.poster ? `<img src="${winnerRaw.poster}" style="width:54px; height:80px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.12);">` : ''}
        <div>
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            ${rating ? `<span class="badge" style="background:rgba(255,255,255,0.09); color:#fafafa; border-color:rgba(255,255,255,0.18); font-weight:600;">★ ${rating} IMDb</span>` : ''}
            ${year ? `<span style="font-size:0.78rem; color:var(--muted);">${year}</span>` : ''}
            ${genres.map(g => `<span class="badge">${g}</span>`).join('')}
          </div>
          <div style="font-size:1.12rem; font-weight:700; color:#fafafa; margin-top:5px;">${winnerRaw.name}</div>
          ${winnerRaw.description ? `<div style="font-size:0.76rem; color:var(--muted); margin-top:3px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; max-width:470px;">${winnerRaw.description}</div>` : ''}
        </div>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn" onclick="casePlayOneClick(event, this)">Play</button>
        <button class="btn btn-secondary" onclick="caseOpenDetails()">Fiche &amp; Sources</button>
        <button class="btn btn-secondary" onclick="caseOpenTrailer()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>Bande-annonce</button>
        <button class="btn btn-secondary" id="caseWlBtn" onclick="caseToggleWatchlist(this)" title="Ajouter ou retirer ce titre de Ma Liste">${inWl ? '✓ Dans Ma Liste' : '+ Ma Liste'}</button>
        ${poolMode !== 'watched' ? `<button class="btn btn-secondary" onclick="caseMarkWatchedAndRespin(this)" title="Marquer ce titre comme Déjà vu, le retirer de la roulette et relancer un tirage">✓ Déjà vu</button>` : ''}
        <button class="btn-surprise" onclick="surpriseMeMedia('${poolMode || 'catalog'}')">Relancer</button>
      </div>
    `;
    winPanel.style.display = 'flex';
  }
}

async function caseToggleWatchlist(btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m || !m.id) return;
  await toggleWatchlist(null, m);
  if (btnEl) {
    const inWl = isInWatchlist(m.id);
    btnEl.textContent = inWl ? '✓ Dans Ma Liste' : '+ Ma Liste';
  }
}

async function caseMarkWatchedAndRespin(btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m || !m.id) return;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '✓ Ajouté aux déjà vus...';
  }
  try {
    const res = await api('/api/watched-toggle', {
      method: 'POST',
      body: JSON.stringify({
        id: m.id,
        name: m.name || '',
        type: m.type || 'movie',
        year: m.year || '',
        poster: m.poster || '',
        imdbRating: m.imdbRating || '',
        force_watched: true
      })
    });
    userHistory = res.history || [];
    updateListBadges();
    renderHomeResume();
    if (activeTab === 'watchlist' || activeTab === 'watched') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
    // Relancer immédiatement la roulette dans le même mode sans ce film
    surpriseMeMedia(m.poolMode || 'catalog');
  } catch (e) {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = '✓ Déjà vu';
    }
  }
}

function casePlayOneClick(ev, btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  oneClickCard(ev, btnEl, m);
}

function caseOpenDetails() {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  selectMedia(m);
}

function caseOpenTrailer() {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  openTrailerModal('', m.name || 'KINO', m.year || '', 'vf');
}

/* ============================================================================
 * IMPORTATION WATCHLIST & FILMS DÉJÀ VUS LETTERBOXD
 * ============================================================================ */
let letterboxdCsvContent = '';
let letterboxdCsvName = '';

function openLetterboxdModal() {
  const m = document.getElementById('letterboxdModal');
  const st = document.getElementById('lbxImportStatus');
  if (st) { st.style.display = 'none'; st.textContent = ''; }
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '';
  if (clrBtn) clrBtn.style.display = 'none';
  letterboxdCsvContent = '';
  letterboxdCsvName = '';

  const inp = document.getElementById('lbxUrlInput');
  if (inp && !inp.value && window.savedLetterboxdUser) {
    inp.value = window.savedLetterboxdUser;
  }

  if (m) {
    m.style.display = 'flex';
    m.classList.add('active');
    if (inp) setTimeout(() => { inp.focus(); inp.select(); }, 50);
  }
}

function closeLetterboxdModal() {
  const m = document.getElementById('letterboxdModal');
  if (m) {
    m.style.display = 'none';
    m.classList.remove('active');
  }
}

function handleLetterboxdCsvFile(files) {
  if (!files || !files.length) return;
  const file = files[0];
  letterboxdCsvName = file.name;
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '✓ ' + file.name;
  if (clrBtn) clrBtn.style.display = 'inline';
  const reader = new FileReader();
  reader.onload = () => {
    letterboxdCsvContent = String(reader.result || '');
  };
  reader.readAsText(file, 'utf-8');
}

function clearLetterboxdCsv(ev) {
  if (ev) ev.stopPropagation();
  letterboxdCsvContent = '';
  letterboxdCsvName = '';
  const fileInp = document.getElementById('lbxCsvFileInput');
  if (fileInp) fileInp.value = '';
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '';
  if (clrBtn) clrBtn.style.display = 'none';
}

function updateListBadges() {
  const wlEl = document.getElementById('wlCount');
  if (wlEl) wlEl.textContent = userWatchlist.length ? `(${userWatchlist.length})` : '';
  const inProg = getInProgressHistory();
  const histEl = document.getElementById('histCount');
  if (histEl) histEl.textContent = inProg.length ? `(${inProg.length})` : '';
  const watchedList = getWatchedHistory();
  const watchedEl = document.getElementById('watchedCount');
  if (watchedEl) watchedEl.textContent = watchedList.length ? `(${watchedList.length})` : '';
  const clEl = document.getElementById('customListsCount');
  if (clEl && window.customLists) clEl.textContent = window.customLists.length ? `(${window.customLists.length})` : '';
  const total = userWatchlist.length + inProg.length + watchedList.length;
  const libTotalEl = document.getElementById('libraryTotalBadge');
  if (libTotalEl) libTotalEl.textContent = total > 0 ? `(${total})` : '';
}

async function toggleCardWatched(ev, media) {
  if (ev) ev.stopPropagation();
  if (!media || !media.id) return;
  try {
    const res = await api('/api/watched-toggle', {
      method: 'POST',
      body: JSON.stringify({
        id: media.id,
        name: media.name || '',
        type: media.type || 'movie',
        year: media.year || '',
        poster: media.poster || '',
        imdbRating: media.imdbRating || ''
      })
    });
    userHistory = res.history || [];
    updateListBadges();
    renderHomeResume();
    if (activeTab === 'watched' || activeTab === 'watchlist') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
  } catch (e) {
    console.warn(e);
  }
}

async function submitLetterboxdImport() {
  const urlOrUser = (document.getElementById('lbxUrlInput')?.value || '').trim();
  const csvText = (letterboxdCsvContent || '').trim();
  const st = document.getElementById('lbxImportStatus');
  const btn = document.getElementById('lbxSubmitBtn');

  if (!urlOrUser && !csvText) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Indiquez votre pseudo Letterboxd ou choisissez un fichier .csv.';
      st.style.display = 'block';
    }
    return;
  }

  const origTxt = btn ? btn.textContent : 'Synchroniser';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Synchronisation...';
  }
  if (st) {
    st.style.color = '#fbbf24';
    st.textContent = 'Récupération de Letterboxd et mise à jour de la bibliothèque...';
    st.style.display = 'block';
  }

  try {
    const res = await api('/api/import-letterboxd', {
      method: 'POST',
      body: JSON.stringify({
        url_or_user: urlOrUser,
        csv_text: csvText,
        csv_filename: letterboxdCsvName,
        mode: 'both'
      })
    });
    userWatchlist = res.watchlist || [];
    if (res.history) userHistory = res.history;
    if (urlOrUser && !urlOrUser.startsWith('http') && !urlOrUser.includes('/')) {
      window.savedLetterboxdUser = urlOrUser.replace(/^@/, '');
    }
    updateListBadges();
    renderHomeResume();
    if (activeTab === 'watchlist' || activeTab === 'watched') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
    if (st) {
      st.style.color = '#00e054';
      const parts = [];
      if (res.added_wl_count > 0 || res.found_wl_count > 0) {
        parts.push(`${res.added_wl_count} dans Ma Liste`);
      }
      if (res.added_watched_count > 0 || res.found_watched_count > 0) {
        parts.push(`${res.added_watched_count} dans Déjà vus`);
      }
      st.textContent = `✓ Synchronisation réussie : ${parts.join(', ') || (res.found_count + ' films')} !`;
      st.style.display = 'block';
    }
    setTimeout(() => {
      closeLetterboxdModal();
    }, 1200);
  } catch (e) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Erreur : ' + e.message;
      st.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origTxt;
    }
  }
}

let userCustomLists = [];

function switchLbxModalTab(tab) {
  const btnProf = document.getElementById('lbxTabBtnProfile');
  const btnList = document.getElementById('lbxTabBtnList');
  const secProf = document.getElementById('lbxSectionProfile');
  const secList = document.getElementById('lbxSectionList');
  if (tab === 'profile') {
    if (btnProf) btnProf.classList.add('active');
    if (btnList) btnList.classList.remove('active');
    if (secProf) secProf.style.display = 'block';
    if (secList) secList.style.display = 'none';
  } else {
    if (btnList) btnList.classList.add('active');
    if (btnProf) btnProf.classList.remove('active');
    if (secList) secList.style.display = 'block';
    if (secProf) secProf.style.display = 'none';
    renderSavedListsInModal();
  }
}

async function fetchCustomLists() {
  try {
    const data = await api('/api/letterboxd/custom-lists');
    userCustomLists = data.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';
  } catch (e) {
    console.warn(e);
  }
}

function renderSavedListsInModal() {
  const wrap = document.getElementById('lbxSavedListsWrap');
  const container = document.getElementById('lbxSavedListsContainer');
  if (!wrap || !container) return;
  if (!userCustomLists.length) {
    wrap.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  wrap.style.display = 'block';
  container.innerHTML = userCustomLists.map(l => `
    <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.03); border:1px solid var(--border); padding:6px 10px; border-radius:6px;">
      <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:320px;">
        <span style="font-size:0.78rem; font-weight:600; color:#fafafa;">${l.title}</span>
        <span style="font-size:0.72rem; color:var(--muted); margin-left:6px;">(${l.count} films)</span>
      </div>
      <button type="button" class="btn btn-secondary" style="padding:2px 7px; font-size:0.7rem; color:#f87171;" onclick="deleteLetterboxdCustomList('${l.id}')">✕</button>
    </div>
  `).join('');
}

async function submitLetterboxdCustomListImport() {
  const urlInp = document.getElementById('lbxCustomListUrlInput');
  const url = (urlInp?.value || '').trim();
  const st = document.getElementById('lbxCustomListStatus');
  const btn = document.getElementById('lbxCustomListSubmitBtn');

  if (!url) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Veuillez coller une URL de liste Letterboxd valide.';
      st.style.display = 'block';
    }
    return;
  }

  const origTxt = btn ? btn.textContent : 'Importer cette liste';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Importation en cours...';
  }
  if (st) {
    st.style.color = '#fbbf24';
    st.textContent = 'Analyse de la liste et résolution des métadonnées (1-2 min max)...';
    st.style.display = 'block';
  }

  try {
    const res = await api('/api/letterboxd/import-list', {
      method: 'POST',
      body: JSON.stringify({ url })
    });
    userCustomLists = res.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';

    if (st) {
      st.style.color = '#00e054';
      st.textContent = `✓ Liste "${res.list.title}" importée avec succès (${res.total_resolved} films résolus) !`;
      st.style.display = 'block';
    }
    renderSavedListsInModal();
    if (urlInp) urlInp.value = '';

    if (activeTab === 'customlists') {
      renderCustomListsTab();
    }
  } catch (e) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Erreur : ' + e.message;
      st.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origTxt;
    }
  }
}

async function deleteLetterboxdCustomList(listId) {
  if (!confirm('Voulez-vous vraiment retirer cette liste importée ?')) return;
  try {
    const res = await api('/api/letterboxd/delete-list', {
      method: 'POST',
      body: JSON.stringify({ id: listId })
    });
    userCustomLists = res.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';
    renderSavedListsInModal();
    if (activeTab === 'customlists') {
      renderCustomListsTab();
    }
  } catch (e) {
    console.warn(e);
  }
}

function renderCustomListsTab() {
  const grid = document.getElementById('postersGrid');
  const catTitle = document.getElementById('catalogTitle');
  const lm = document.getElementById('loadMoreWrap');
  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const wlw = document.getElementById('watchlistActionsWrap');
  const hs = document.getElementById('heroSpotlight');

  if (gf) gf.style.display = 'none';
  if (sw) sw.style.display = 'none';
  if (wlw) wlw.style.display = 'none';
  if (lm) lm.style.display = 'none';
  if (hs) hs.style.display = 'none';
  document.getElementById('homeResumeSection').style.display = 'none';

  if (catTitle) {
    catTitle.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; width:100%; flex-wrap:wrap; gap:10px;">
        <span>Listes Letterboxd importées</span>
        <button class="btn btn-secondary" onclick="openLetterboxdModal(); switchLbxModalTab('list');" style="display:inline-flex; align-items:center; gap:6px; padding:5px 12px; font-size:0.78rem; border-color:rgba(0,224,84,0.4);">
          <span>+</span> Importer une nouvelle liste
        </button>
      </div>
    `;
  }

  if (!userCustomLists.length) {
    grid.style.display = 'block';
    grid.innerHTML = `
      <div style="padding:48px 24px; text-align:center; max-width:540px; margin:0 auto; background:rgba(255,255,255,0.02); border:1px dashed var(--border); border-radius:10px;">
        <svg width="40" height="20" viewBox="0 0 30 12" fill="none" style="margin-bottom:12px;">
          <circle cx="6" cy="6" r="5" fill="#ff8000"/>
          <circle cx="15" cy="6" r="5" fill="#00e054"/>
          <circle cx="24" cy="6" r="5" fill="#40bcf4"/>
        </svg>
        <h3 style="font-size:1.05rem; font-weight:600; margin:0 0 8px 0; color:#fafafa;">Aucune liste Letterboxd importée</h3>
        <p style="color:var(--muted); font-size:0.82rem; line-height:1.5; margin:0 0 18px 0;">
          Collez le lien de n'importe quelle liste Letterboxd publique (Top 250, A24, films par ambiance ou réalisateur) pour la transformer en collection streamable avec tirage Roulette dédié !
        </p>
        <button class="btn" onclick="openLetterboxdModal(); switchLbxModalTab('list');" style="padding:8px 18px; font-size:0.82rem;">
          Importer une liste Letterboxd
        </button>
      </div>
    `;
    return;
  }

  grid.style.display = 'flex';
  grid.style.flexDirection = 'column';
  grid.style.gap = '32px';

  grid.innerHTML = userCustomLists.map(list => {
    const items = list.items || [];
    return `
      <div class="custom-list-shelf" style="display:flex; flex-direction:column; gap:12px; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:24px;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
          <div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:1.02rem; font-weight:700; color:#fafafa;">${list.title}</span>
              <span class="badge" style="font-size:0.7rem; background:rgba(0,224,84,0.12); color:#00e054; border-color:rgba(0,224,84,0.25);">${items.length} films</span>
            </div>
            ${list.description ? `<p style="color:var(--muted); font-size:0.76rem; margin:3px 0 0 0; max-width:650px;">${list.description}</p>` : ''}
          </div>
          <div style="display:flex; gap:8px; align-items:center;">
            <button class="btn-surprise" onclick="surpriseMeFromCustomList('${list.id}')" title="Lancer la roulette KINO sur cette liste">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="4" width="20" height="16" rx="3"/>
                <line x1="12" y1="4" x2="12" y2="20"/>
              </svg>
              <span>Roulette sur cette liste</span>
            </button>
            <a href="${list.url}" target="_blank" class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem;">Letterboxd ↗</a>
            <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.74rem; color:#f87171;" onclick="deleteLetterboxdCustomList('${list.id}')" title="Retirer cette liste">✕</button>
          </div>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(135px, 1fr)); gap:12px;">
          ${items.map(m => {
            const inWl = isInWatchlist(m.id);
            const isW = isWatchedMedia(m.id);
            const ratingVal = parseFloat(m.imdbRating || '0') || 0;
            return `
              <div class="card" onclick="selectMedia({id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})">
                <div class="card-thumb">
                  ${m.poster ? `<img class="card-img" src="${m.poster}" alt="${escapeJsString(m.name)}" loading="lazy">` : `<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:var(--dim); font-size:0.75rem;">KINO</div>`}
                  ${ratingVal > 0 ? `<div class="card-rating">★ ${m.imdbRating}</div>` : ''}
                  <div class="card-actions">
                    <button class="card-action-btn ${isW ? 'active' : ''}" onclick="toggleCardWatched(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${isW ? 'Marqué comme vu' : 'Marquer comme vu'}">${isW ? '✓' : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px;"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'}</button>
                    <button class="card-action-btn ${inWl ? 'active' : ''}" onclick="toggleWatchlist(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${inWl ? 'Dans Ma Liste' : 'Ajouter à Ma Liste'}">${inWl ? '✓' : '+'}</button>
                  </div>
                  <button class="card-play-btn" onclick="oneClickCard(event, this, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="Lecture 1-Clic">Play</button>
                </div>
                <div class="card-title" title="${escapeJsString(m.name)}">${m.name || 'Sans titre'}</div>
                <div class="card-sub">${m.year || 'Film'}</div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }).join('');
}

function surpriseMeFromCustomList(listId) {
  const l = userCustomLists.find(x => x.id === listId);
  if (!l || !l.items || !l.items.length) {
    alert('Cette liste ne contient aucun film.');
    return;
  }
  const unviewed = l.items.filter(m => !isWatchedMedia(m.id));
  const pool = unviewed.length ? unviewed : l.items;
  surpriseMeMedia({ items: pool, title: l.title });
}

let lastActiveLibraryTab = 'watchlist';

async function switchTab(tab) {
  const isLibraryTab = ['watchlist', 'history', 'watched', 'customlists', 'rdcloud', 'library'].includes(tab);
  if (isLibraryTab) {
    if (tab === 'library') {
      tab = lastActiveLibraryTab || 'watchlist';
    } else {
      lastActiveLibraryTab = tab;
    }
  }

  activeTab = tab;

  // Active state for primary tabs
  document.querySelectorAll('.nav-tabs-primary .nav-tab').forEach(t => t.classList.remove('active'));
  if (isLibraryTab) {
    const libEl = document.getElementById('tab-library');
    if (libEl) libEl.classList.add('active');
  } else if (tab === 'classics') {
    const movEl = document.getElementById('tab-movies');
    if (movEl) movEl.classList.add('active');
  } else {
    const activeEl = document.getElementById('tab-' + tab);
    if (activeEl) activeEl.classList.add('active');
  }

  // Subnav visibility & active state
  const libSubNav = document.getElementById('librarySubNav');
  if (libSubNav) {
    libSubNav.style.display = isLibraryTab ? 'flex' : 'none';
  }
  if (isLibraryTab) {
    document.querySelectorAll('.lib-subtab').forEach(t => t.classList.remove('active'));
    const activeSub = document.getElementById('subtab-' + tab);
    if (activeSub) activeSub.classList.add('active');
  }

  document.getElementById('detailPanel').style.display = 'none';
  document.getElementById('torrentsPanel').style.display = 'none';
  document.getElementById('rdCloudPanel').style.display = 'none';
  const comP = document.getElementById('communityPanel');
  if (comP) comP.style.display = 'none';
  const aniP = document.getElementById('animePanel');
  if (aniP) aniP.style.display = 'none';
  document.getElementById('postersGrid').style.display = 'grid';
  document.getElementById('catalogHeader').style.display = 'flex';
  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const wlw = document.getElementById('watchlistActionsWrap');
  const lm = document.getElementById('loadMoreWrap');
  const hs = document.getElementById('heroSpotlight');
  const statsEl = document.getElementById('listStatsBadge');
  const surpriseLbl = document.getElementById('listSurpriseBtnLabel');

  await refreshUserLists();

  if (tab === 'movies') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('searchType').value = 'movie';
    renderGenreChipsForTab('movies');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'series') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('searchType').value = 'series';
    renderGenreChipsForTab('series');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'classics') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('searchType').value = 'movie';
    renderGenreChipsForTab('classics');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'anime') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('searchType').value = 'anime';
    renderGenreChipsForTab('anime');
    activeGenre = '';
    selectGenre('', document.querySelector('#genreFilters .chip'));
  } else if (tab === 'watchlist') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = 'Tirage sur Ma Liste';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Ma Liste';
    renderActiveListTab();
  } else if (tab === 'watched') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = 'Tirage Rewatch';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Déjà vus';
    renderActiveListTab();
  } else if (tab === 'customlists') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'flex';
    document.getElementById('postersGrid').style.display = 'flex';
    renderCustomListsTab();
  } else if (tab === 'history') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    renderHomeResume();
  } else if (tab === 'rdcloud') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    document.getElementById('rdCloudPanel').style.display = 'block';
    await loadRdCloud();
  } else if (tab === 'community') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    if (comP) comP.style.display = 'block';
    await loadCommunityTab();
  }
}

async function loadCatalog(type, genre = '', reset = true) {
  const grid = document.getElementById('postersGrid');
  const lm = document.getElementById('loadMoreWrap');
  if (reset) {
    catalogSkip = 0;
    catalogItems = [];
    grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement du catalogue...</p>';
    if (lm) lm.style.display = 'none';
  }
  try {
    const data = await api(`/api/catalog?type=${encodeURIComponent(type)}&genre=${encodeURIComponent(genre || '')}&sort=${encodeURIComponent(catalogSort)}&skip=${catalogSkip}`);
    const incoming = data.metas || [];
    const seenIds = new Set(catalogItems.map(x => x.id));
    for (const m of incoming) {
      if (m && m.id && !seenIds.has(m.id)) {
        seenIds.add(m.id);
        catalogItems.push(m);
      }
    }
    if (type === 'classics' && !genre) {
      classicsCatalogCache = [...catalogItems];
    }
    if (reset) {
      renderHeroSpotlight(catalogItems, type === 'classics' ? 'movie' : type);
    }
    if (type === 'classics') {
      updateClassicsStatsBadge();
    }
    renderPosterCards(catalogItems, type === 'classics' ? 'movie' : type);
    if (lm) {
      lm.style.display = (type !== 'classics' && incoming.length >= 20) ? 'flex' : 'none';
    }
  } catch (e) {
    if (reset) {
      grid.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
    }
  }
}

async function loadMoreCatalog() {
  const btn = document.getElementById('loadMoreBtn');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Chargement...'; }
  catalogSkip += 50;
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  try {
    await loadCatalog(type, activeGenre, false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig || 'Voir plus de titres'; }
  }
}

function renderPosterCards(metas, fallbackType) {
  const grid = document.getElementById('postersGrid');
  let displayList = metas || [];
  if (hideWatchedInCatalog && (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics' || activeTab === 'anime')) {
    displayList = displayList.filter(m => {
      const h = userHistory.find(x => x.id === m.id);
      return !(h && (h.completed || Number(h.progress_pct || 0) >= 85));
    });
  }
  if (!displayList.length) {
    grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Aucun élément à afficher.</p>';
    return;
  }
  grid.innerHTML = displayList.map(m => {
    const isAnime = Boolean(activeTab === 'anime' || m.is_anime);
    const mtype = (m.type === 'movie' || (!m.type && isAnime && m.is_movie)) ? 'movie' : (isAnime ? 'series' : (m.type || fallbackType || 'movie'));
    const year = m.releaseInfo || m.year || '';
    const histItem = userHistory.find(h => h.id === m.id);
    const ratingVal = m.imdbRating || (histItem && histItem.imdbRating) || '';
    const mediaObj = {
      id: m.id,
      name: m.name,
      type: mtype,
      is_anime: isAnime,
      year: String(year),
      poster: m.poster || '',
      imdbRating: String(ratingVal)
    };
    const payload = JSON.stringify(mediaObj).replace(/'/g, "&#39;");
    const inList = isInWatchlist(m.id);
    const isMovieWatched = Boolean(histItem && mtype !== 'series' && (histItem.completed || Number(histItem.progress_pct || 0) >= 85));
    const watchedEpsCount = (histItem && mtype === 'series' && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes.length : 0;
    const watchedPill = isMovieWatched
      ? `<button type="button" onclick='toggleCardWatched(event, ${payload})' title="Cliquer pour retirer des Déjà vus" style="position:absolute; top:8px; left:8px; background:rgba(9,9,11,0.92); color:#4ade80; border:1px solid rgba(74,222,128,0.55); font-size:0.68rem; font-weight:600; padding:2px 7px; border-radius:4px; z-index:2; cursor:pointer;">✓ Vu</button>`
      : (watchedEpsCount > 0 ? `<span style="position:absolute; top:8px; left:8px; background:rgba(9,9,11,0.88); color:#fafafa; border:1px solid var(--border-hover); font-size:0.68rem; font-weight:600; padding:2px 6px; border-radius:4px; z-index:2; pointer-events:none;">✓ ${watchedEpsCount} ép.</span>` : '');
    let btnLabel = 'Play';
    if (histItem && mtype === 'series' && histItem.season && histItem.episode) {
      const targetEp = getSeriesTargetEpisode(histItem);
      const code = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
      btnLabel = targetEp.isNext ? `▶ Suivant ${code}` : `Play ${code}`;
    } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85 && !isMovieWatched) {
      btnLabel = `Reprendre (${Math.round(histItem.progress_pct)}%)`;
    } else if (isMovieWatched) {
      btnLabel = 'Revoir';
    }
    const cardProg = (histItem && histItem.progress_pct > 0 && !isMovieWatched) ? `
      <div style="height:3px; background:var(--surface-2); width:100%;">
        <div style="height:100%; width:${Math.min(100, histItem.progress_pct)}%; background:var(--text);"></div>
      </div>
    ` : '';
    return `
      <div class="poster-card" onclick='selectMedia(${payload})'>
        ${watchedPill}
        <button class="wl-btn ${inList ? 'in-list' : ''}" data-wl-id="${m.id}" title="${inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste'}" onclick='toggleWatchlist(event, ${payload})'>${inList ? '✓' : '+'}</button>
        <img src="${m.poster || ''}" alt="${m.name}" loading="lazy" onerror="this.style.opacity=0.08">
        ${cardProg}
        <div class="poster-info">
          <div class="poster-title">${m.name}</div>
          <div class="poster-year">${year}${ratingVal ? ' • ★ ' + ratingVal : ''}${(activeTab === 'anime' || m.is_anime) ? ' • <span style="color:#38bdf8; font-weight:600;">VOSTFR</span>' : ''}</div>
          <button class="btn-oneclick" onclick='oneClickCard(event, this, ${payload})'>${btnLabel}</button>
        </div>
      </div>
    `;
  }).join('');
}

async function loadRdCloud() {
  const list = document.getElementById('rdCloudList');
  const pName = window.activeDebridName || 'votre débrideur';
  list.innerHTML = `<p style="color:var(--dim); font-size:0.84rem;">Récupération de votre historique ${pName}...</p>`;
  try {
    const data = await api('/api/rd-history');
    const items = data.items || [];
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && data.cleaned && (data.cleaned.deleted_downloads > 0 || data.cleaned.deleted_torrents > 0)) {
      noteEl.textContent = `Nettoyage auto effectué : ${data.cleaned.deleted_downloads} fichier(s) et ${data.cleaned.deleted_torrents} torrent(s) expirés supprimés de ${pName}.`;
    }
    if (!items.length) {
      list.innerHTML = `<p style="color:var(--dim); font-size:0.84rem;">Aucun fichier récent sur votre compte ${pName}.</p>`;
      return;
    }
    list.innerHTML = items.map(f => {
      const idsPayload = JSON.stringify(f.ids || [f.id]);
      return `
      <div class="torrent-item">
        <div style="flex:1; min-width:240px;">
          <div class="torrent-title">${f.filename}</div>
          <div class="torrent-meta">${f.filesize}${f.generated ? ' • ' + f.generated : ''}</div>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
          <button class="btn" onclick='openMpv(this, ${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Play</button>
          <button class="btn btn-secondary" onclick='startPcDownload(${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Télécharger</button>
          <a class="btn btn-secondary" href="${f.download}" target="_blank">Lien direct</a>
          <button class="btn btn-secondary" style="padding:6px 10px;" title="Supprimer du Cloud" onclick='deleteRdCloudItem(this, ${idsPayload})'>✕</button>
        </div>
      </div>
    `}).join('');
  } catch (e) {
    list.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
  }
}

async function deleteRdCloudItem(btn, ids) {
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    await api('/api/rd-delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids})
    });
    await loadRdCloud();
  } catch (e) {
    alert('Erreur suppression : ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '✕'; }
  }
}

async function onChangeRdRetention(daysStr) {
  const rd_retention_days = parseInt(daysStr || '0', 10);
  if (document.getElementById('cfgRdRetention')) {
    document.getElementById('cfgRdRetention').value = String(rd_retention_days);
  }
  await api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rd_retention_days})
  });
  await loadRdCloud();
}

async function purgeRdCloud(mode, btn) {
  const orig = btn ? btn.textContent : '';
  const pName = window.activeDebridName || 'votre débrideur';
  if (btn) { btn.disabled = true; btn.textContent = 'Suppression...'; }
  try {
    const res = await api('/api/rd-cleanup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({max_age_days: mode === 'all' ? -1 : null})
    });
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && res.cleaned) {
      noteEl.textContent = `${res.cleaned.deleted_downloads} fichier(s) et ${res.cleaned.deleted_torrents} torrent(s) supprimés du Cloud ${pName}.`;
    }
    await loadRdCloud();
  } catch (e) {
    alert('Erreur nettoyage Cloud : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

async function openTrailerModal(trailerId, title, year = '', lang = 'vf') {
  currentTrailerCtx = {trailerId: trailerId || '', title: title || '', year: year || '', lang: lang || 'vf'};
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  const loading = document.getElementById('trailerLoading');
  const titleEl = document.getElementById('trailerModalTitle');
  const extLink = document.getElementById('trailerExternalLink');
  const btnVf = document.getElementById('trailerBtnVf');
  const btnVo = document.getElementById('trailerBtnVo');
  if (!modal) return;

  if (btnVf) btnVf.classList.toggle('active', currentTrailerCtx.lang === 'vf');
  if (btnVo) btnVo.classList.toggle('active', currentTrailerCtx.lang === 'vo');

  titleEl.textContent = `Bande-annonce (${currentTrailerCtx.lang.toUpperCase()}) — ${title || 'KINO'}`;
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (loading) loading.style.display = 'flex';
  modal.style.display = 'flex';

  try {
    const qs = new URLSearchParams({
      title: currentTrailerCtx.title,
      year: currentTrailerCtx.year,
      yt_id: currentTrailerCtx.trailerId,
      lang: currentTrailerCtx.lang
    });
    const data = await api(`/api/trailer?${qs.toString()}`);
    if (extLink && data.watch_url) extLink.href = data.watch_url;
    if (loading) loading.style.display = 'none';
    if (data.stream_url && video) {
      video.src = data.stream_url;
      video.style.display = 'block';
      video.play().catch(() => {});
    } else if (data.embed_url && iframe) {
      iframe.src = data.embed_url;
      iframe.style.display = 'block';
    }
  } catch (e) {
    if (loading) loading.style.display = 'none';
    if (trailerId && iframe) {
      extLink.href = `https://www.youtube.com/watch?v=${encodeURIComponent(trailerId)}`;
      iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(trailerId)}?autoplay=1&rel=0`;
      iframe.style.display = 'block';
    }
  }
}

function switchTrailerLang(lang) {
  if (!currentTrailerCtx) return;
  openTrailerModal(currentTrailerCtx.trailerId, currentTrailerCtx.title, currentTrailerCtx.year, lang);
}

function closeTrailerModal() {
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (modal) modal.style.display = 'none';
}

function toggleHeaderMenu(e) {
  if (e) e.stopPropagation();
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  if (!menu) return;
  const isShown = (menu.style.display === 'flex');
  if (isShown) {
    menu.style.display = 'none';
    if (wrap) wrap.classList.remove('active');
  } else {
    menu.style.display = 'flex';
    if (wrap) wrap.classList.add('active');
  }
}

function closeHeaderMenu() {
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  if (menu) menu.style.display = 'none';
  if (wrap) wrap.classList.remove('active');
}

window.addEventListener('click', (e) => {
  const wrap = document.querySelector('.header-menu-wrap');
  if (wrap && !wrap.contains(e.target)) {
    closeHeaderMenu();
  }
});

function openConfig() { 
  document.getElementById('configModal').style.display = 'flex'; 
  triggerGdriveSync();
  checkTraktStatus();
}
function closeConfig() { document.getElementById('configModal').style.display = 'none'; }

async function triggerGdriveSync(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'En cours...'; }
  try {
    const res = await api('/api/sync/gdrive');
    const el = document.getElementById('gdriveSyncStatus');
    if (res && res.status === 'synced') {
      const d = new Date((res.updated_at || Date.now()/1000) * 1000);
      const timeStr = d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      if (el) el.innerHTML = `<span style="color:#4ade80;">✓ Synchronisé (${res.history_count || 0} films, ${res.watchlist_count || 0} favoris) à ${timeStr}</span>`;
      if (typeof loadUserData === 'function') await loadUserData();
    } else {
      if (el) el.innerHTML = `<span style="color:var(--muted);">${(res && res.message) ? res.message : 'Google Drive non détecté'}</span>`;
    }
  } catch(e) {
    const el = document.getElementById('gdriveSyncStatus');
    if (el) el.innerHTML = `<span style="color:#ef4444;">Erreur : ${e.message}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Synchroniser'; }
  }
}

async function saveConfig() {
  const debrid_provider = document.getElementById('cfgProvider') ? document.getElementById('cfgProvider').value : 'realdebrid';
  const token = document.getElementById('cfgToken').value.trim();
  const dir = document.getElementById('cfgDir').value.trim();
  const player_mode = document.getElementById('cfgPlayerMode') ? document.getElementById('cfgPlayerMode').value : 'kino';
  const pref_lang = document.getElementById('cfgPrefLang') ? document.getElementById('cfgPrefLang').value : 'vf';
  const pref_quality = document.getElementById('cfgPrefQuality') ? document.getElementById('cfgPrefQuality').value : '4k';
  const hdr_mode = document.getElementById('cfgHdrMode') ? document.getElementById('cfgHdrMode').value : 'sdr_pref';
  const audio_mode = document.getElementById('cfgAudioMode') ? document.getElementById('cfgAudioMode').value : 'voice_boost';
  const rd_retention_days = document.getElementById('cfgRdRetention') ? parseInt(document.getElementById('cfgRdRetention').value || '0', 10) : 0;
  await api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({debrid_provider, rd_token: token, download_dir: dir, player_mode, pref_lang, pref_quality, hdr_mode, audio_mode, rd_retention_days})
  });
  window.kinoPlayerMode = player_mode;
  window.kinoHdrMode = hdr_mode;
  window.kinoAudioMode = audio_mode;
  document.getElementById('cfgToken').value = '';
  closeConfig();
  await checkConfig();
  if (activeTab === 'rdcloud') loadRdCloud();
}

async function openFolder() {
  await api('/api/open-folder', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
}

// ==========================================
// --- KINO SOCIAL, REMOTE, TRAKT & ADDONS ---
// ==========================================

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeJsString(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"');
}

// --- Trakt.tv Integration ---
let traktAuthPollTimer = null;
let traktScrobbledStarted = false;
let traktScrobbledStop = false;

async function checkTraktStatus() {
  try {
    const res = await api('/api/trakt/status');
    const statusEl = document.getElementById('traktSyncStatus');
    const connectBtn = document.getElementById('traktConnectBtn');
    const syncBtn = document.getElementById('traktSyncBtn');
    const authBox = document.getElementById('traktAuthBox');
    
    if (res && res.authenticated) {
      if (statusEl) statusEl.innerHTML = `<span style="color:#4ade80;">✓ Connecté (${res.username ? '@' + res.username : 'Actif'})</span>`;
      if (connectBtn) { connectBtn.textContent = 'Déconnecter'; connectBtn.style.color = '#ef4444'; }
      if (syncBtn) syncBtn.style.display = 'inline-block';
      if (authBox) authBox.style.display = 'none';
      if (traktAuthPollTimer) { clearInterval(traktAuthPollTimer); traktAuthPollTimer = null; }
    } else {
      if (statusEl) statusEl.textContent = 'Non connecté';
      if (connectBtn) { connectBtn.textContent = 'Connecter'; connectBtn.style.color = ''; }
      if (syncBtn) syncBtn.style.display = 'none';
    }
    return Boolean(res && res.authenticated);
  } catch (e) {
    return false;
  }
}

async function toggleTraktAuth(btn) {
  const statusEl = document.getElementById('traktSyncStatus');
  const authBox = document.getElementById('traktAuthBox');
  const codeEl = document.getElementById('traktUserCode');
  const cdEl = document.getElementById('traktAuthCountdown');

  if (btn && btn.textContent.trim() === 'Déconnecter') {
    if (!confirm('Voulez-vous déconnecter votre compte Trakt.tv ?')) return;
    try {
      await api('/api/trakt/disconnect', {method: 'POST'});
      await checkTraktStatus();
    } catch(e) {
      alert('Erreur déconnexion Trakt : ' + e.message);
    }
    return;
  }

  try {
    if (btn) { btn.disabled = true; btn.textContent = 'Connexion...'; }
    const res = await api('/api/trakt/auth/start', {method: 'POST'});
    if (res && res.user_code) {
      if (codeEl) codeEl.textContent = res.user_code;
      if (authBox) authBox.style.display = 'block';
      let expires = res.expires_in || 600;
      if (cdEl) cdEl.textContent = `En attente de validation (expire dans ${Math.round(expires/60)} min)...`;

      if (traktAuthPollTimer) clearInterval(traktAuthPollTimer);
      traktAuthPollTimer = setInterval(async () => {
        try {
          const pollRes = await api('/api/trakt/auth/poll', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({device_code: res.device_code})
          });
          if (pollRes && pollRes.status === 'authenticated') {
            clearInterval(traktAuthPollTimer);
            traktAuthPollTimer = null;
            if (authBox) authBox.style.display = 'none';
            await checkTraktStatus();
            alert('Compte Trakt.tv connecté avec succès ! Scrobble automatique et synchronisation activés.');
          } else if (pollRes && (pollRes.status === 'expired' || pollRes.status === 'denied')) {
            clearInterval(traktAuthPollTimer);
            traktAuthPollTimer = null;
            if (cdEl) cdEl.textContent = 'Code expiré ou refusé. Réessayez.';
          }
        } catch(err) {}
      }, (res.interval || 5) * 1000);
    }
  } catch(e) {
    alert('Erreur initialisation Trakt : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Connecter'; }
  }
}

async function triggerTraktSync(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Sync...'; }
  try {
    const res = await api('/api/trakt/sync', {method: 'POST'});
    if (res && res.success) {
      alert(`Synchronisation Trakt terminée !\n${res.added_to_kino || 0} éléments ajoutés à votre liste KINO.\n${res.synced_to_trakt || 0} éléments synchronisés vers Trakt.`);
      await refreshUserLists();
    } else {
      alert('Erreur lors de la synchronisation Trakt : ' + (res.error || 'inconnue'));
    }
  } catch(e) {
    alert('Erreur Trakt sync : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Synchroniser'; }
  }
}

async function scrobbleTrakt(action, media, progressPct) {
  try {
    if (!media) return;
    const name = media.name || media.title || '';
    if (!name) return;
    const s = media.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
    const e = media.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
    await api('/api/trakt/scrobble', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action: action,
        title: name,
        year: media.year || '',
        type: media.type || 'movie',
        season: s,
        episode: e,
        progress: progressPct || 0
      })
    });
  } catch(err) {}
}

// --- KINO Remote Smartphone ---
let remotePollingInterval = null;

async function openRemoteModal() {
  const modal = document.getElementById('remoteModal');
  const qrBox = document.getElementById('remoteQrContainer');
  const urlBox = document.getElementById('remoteDirectUrl');
  if (!modal) return;
  modal.style.display = 'flex';
  
  if (qrBox) {
    qrBox.innerHTML = '<div style="color:#71717a; font-size:0.8rem; margin:auto;">Génération du QR Code local...</div>';
  }
  try {
    const res = await api('/api/remote/qr');
    const remoteUrl = (res && res.url) ? res.url : (`http://${window.location.hostname || '127.0.0.1'}:${window.location.port || '8080'}/remote`);
    if (urlBox) {
      urlBox.textContent = remoteUrl;
    }
    if (qrBox) {
      qrBox.innerHTML = '';
      if (typeof QRCode !== 'undefined') {
        new QRCode(qrBox, {
          text: remoteUrl,
          width: 196,
          height: 196,
          colorDark: '#09090b',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.M
        });
      } else {
        qrBox.innerHTML = `<div style="padding:20px; text-align:center;"><a href="${remoteUrl}" target="_blank" style="color:#09090b; font-weight:600; font-size:0.85rem; word-break:break-all;">${remoteUrl}</a></div>`;
      }
    }
  } catch(e) {
    const fallbackUrl = `http://${window.location.hostname || '127.0.0.1'}:${window.location.port || '8080'}/remote`;
    if (urlBox) urlBox.textContent = fallbackUrl;
    if (qrBox) {
      qrBox.innerHTML = '';
      if (typeof QRCode !== 'undefined') {
        new QRCode(qrBox, {
          text: fallbackUrl,
          width: 196,
          height: 196,
          colorDark: '#09090b',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.M
        });
      }
    }
  }
}

function closeRemoteModal() {
  const modal = document.getElementById('remoteModal');
  if (modal) modal.style.display = 'none';
}

function copyRemoteUrl() {
  const urlBox = document.getElementById('remoteDirectUrl');
  if (!urlBox || !urlBox.textContent) return;
  const url = urlBox.textContent.trim();
  if (url.startsWith('http')) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(() => {
        showInAppToast('Adresse de la télécommande copiée !', 2000);
      }).catch(() => {
        prompt('Adresse KINO Remote (copiez avec Ctrl+C) :', url);
      });
    } else {
      prompt('Adresse KINO Remote (copiez avec Ctrl+C) :', url);
    }
  }
}

function startRemotePolling() {
  if (remotePollingInterval) return;
  remotePollingInterval = setInterval(async () => {
    const v = document.getElementById('inAppVideo');
    const overlay = document.getElementById('inAppPlayerOverlay');
    const isPlayerActive = Boolean(overlay && overlay.classList.contains('active') && v && v.src);

    if (isPlayerActive) {
      try {
        const curT = v.currentTime || 0;
        const durT = v.duration || 0;
        const s = (inAppCurrentMedia && inAppCurrentMedia.season) ? inAppCurrentMedia.season : null;
        const e = (inAppCurrentMedia && inAppCurrentMedia.episode) ? inAppCurrentMedia.episode : null;
        await fetch('/api/player/state', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            title: inAppCurrentTitle || (inAppCurrentMedia ? inAppCurrentMedia.name : 'Lecture KINO'),
            season: s,
            episode: e,
            currentTime: curT,
            duration: durT,
            paused: v.paused,
            volume: v.volume,
            muted: v.muted
          })
        });
      } catch(e) {}
    }

    try {
      const res = await fetch('/api/player/commands');
      const data = await res.json();
      if (data && data.commands && data.commands.length > 0) {
        for (const cmd of data.commands) {
          handleRemoteCommand(cmd);
        }
      }
    } catch(e) {}
  }, 1000);
}

function handleRemoteCommand(cmd) {
  const v = document.getElementById('inAppVideo');
  if (!v) return;
  const action = cmd.action;
  if (action === 'play') {
    v.play().catch(() => {});
  } else if (action === 'pause') {
    v.pause();
  } else if (action === 'toggle_play') {
    toggleInAppPlay();
  } else if (action === 'seek') {
    if (cmd.delta !== undefined) {
      v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + Number(cmd.delta)));
    } else if (cmd.time !== undefined) {
      v.currentTime = Math.max(0, Math.min(v.duration || 0, Number(cmd.time)));
    }
  } else if (action === 'volume') {
    if (cmd.level !== undefined) {
      v.volume = Math.max(0, Math.min(1, Number(cmd.level)));
      v.muted = false;
      showInAppToast(`Volume : ${Math.round(v.volume * 100)}%`);
    }
  } else if (action === 'mute') {
    v.muted = !v.muted;
    showInAppToast(v.muted ? 'Son coupé' : `Volume : ${Math.round(v.volume * 100)}%`);
  } else if (action === 'next_ep') {
    if (typeof inAppNextTrack === 'function') inAppNextTrack();
  } else if (action === 'prev_ep') {
    if (typeof inAppPrevTrack === 'function') inAppPrevTrack();
  }
}

// --- Watch Party P2P (PeerJS) ---
let wpPeer = null;
let wpConnections = [];
let wpRoomCode = null;
let wpIsSyncing = false;

function openWatchPartyModal() {
  const modal = document.getElementById('watchPartyModal');
  if (modal) modal.style.display = 'flex';
  updateWpModalUI();
}

function closeWatchPartyModal() {
  const modal = document.getElementById('watchPartyModal');
  if (modal) modal.style.display = 'none';
}

function updateWpModalUI() {
  const setupView = document.getElementById('wpSetupView');
  const activeView = document.getElementById('wpActiveView');
  const codeDisplay = document.getElementById('wpRoomCodeDisplay');
  const countEl = document.getElementById('wpPeersCount');
  
  if (wpRoomCode) {
    if (setupView) setupView.style.display = 'none';
    if (activeView) activeView.style.display = 'block';
    if (codeDisplay) codeDisplay.textContent = wpRoomCode;
    if (countEl) countEl.textContent = String(wpConnections.length + 1);
  } else {
    if (setupView) setupView.style.display = 'block';
    if (activeView) activeView.style.display = 'none';
  }
}

function createWatchPartyRoom() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 6; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  wpRoomCode = code;
  initWpPeer('kino-room-' + code.toLowerCase());
}

function showJoinWatchParty() {
  const box = document.getElementById('wpJoinBox');
  if (box) box.style.display = (box.style.display === 'none' ? 'block' : 'none');
  const inp = document.getElementById('wpJoinCodeInput');
  if (inp) inp.focus();
}

function joinWatchPartyRoom() {
  const inp = document.getElementById('wpJoinCodeInput');
  const code = (inp ? inp.value.trim().toUpperCase() : '');
  if (!code || code.length < 4) {
    alert('Veuillez entrer un code de salle valide (ex: KINO9X).');
    return;
  }
  wpRoomCode = code;
  initWpPeer(null, 'kino-room-' + code.toLowerCase());
}

function copyWpRoomCode() {
  if (!wpRoomCode) return;
  navigator.clipboard.writeText(wpRoomCode).then(() => {
    alert(`Code de salle [ ${wpRoomCode} ] copié ! Partagez-le avec vos amis.`);
  }).catch(() => {});
}

function leaveWatchPartyRoom() {
  if (wpPeer) {
    wpPeer.destroy();
    wpPeer = null;
  }
  wpConnections = [];
  wpRoomCode = null;
  updateWpModalUI();
  const chatBox = document.getElementById('inAppWpChatBox');
  if (chatBox) chatBox.style.display = 'none';
  showInAppToast('Vous avez quitté la Watch Party');
}

function initWpPeer(hostPeerId, joinHostPeerId) {
  if (typeof Peer === 'undefined') {
    alert("PeerJS n'a pas pu être chargé depuis le CDN. Vérifiez votre connexion internet.");
    return;
  }
  if (wpPeer) {
    wpPeer.destroy();
    wpPeer = null;
  }
  wpConnections = [];

  const peerOpts = {
    debug: 1,
    config: {
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:global.stun.twilio.com:3478' }
      ]
    }
  };

  try {
    if (hostPeerId) {
      wpPeer = new Peer(hostPeerId, peerOpts);
    } else {
      wpPeer = new Peer(peerOpts);
    }

    wpPeer.on('open', (id) => {
      updateWpModalUI();
      showInAppToast(`Watch Party prête ! Code : ${wpRoomCode}`);
      if (joinHostPeerId) {
        const conn = wpPeer.connect(joinHostPeerId, { reliable: true });
        setupWpConnection(conn);
      }
    });

    wpPeer.on('connection', (conn) => {
      setupWpConnection(conn);
      const v = document.getElementById('inAppVideo');
      if (v) {
        conn.on('open', () => {
          conn.send({
            type: 'sync',
            time: v.currentTime,
            paused: v.paused,
            title: inAppCurrentTitle || ''
          });
        });
      }
    });

    wpPeer.on('error', (err) => {
      console.warn('Watch Party error:', err);
      if (err.type === 'unavailable-id') {
        createWatchPartyRoom();
      } else {
        alert('Erreur Watch Party P2P : ' + err.message);
      }
    });
  } catch(e) {
    alert('Erreur Watch Party : ' + e.message);
  }
}

function setupWpConnection(conn) {
  conn.on('open', () => {
    if (!wpConnections.includes(conn)) wpConnections.push(conn);
    updateWpModalUI();
    showInAppToast('Un ami a rejoint la Watch Party !');
    const chatBox = document.getElementById('inAppWpChatBox');
    if (chatBox) chatBox.style.display = 'flex';
  });

  conn.on('data', (data) => {
    handleWpIncomingData(data, conn);
  });

  conn.on('close', () => {
    wpConnections = wpConnections.filter(c => c !== conn);
    updateWpModalUI();
    showInAppToast('Un participant a quitté la session');
  });
}

function handleWpIncomingData(data, fromConn) {
  if (!data) return;
  const v = document.getElementById('inAppVideo');
  if (!v) return;

  if (data.type === 'sync' || data.type === 'event') {
    wpIsSyncing = true;
    if (data.time !== undefined && Math.abs(v.currentTime - data.time) > 1.5) {
      v.currentTime = data.time;
    }
    if (data.event === 'play' || (data.type === 'sync' && data.paused === false)) {
      v.play().catch(() => {});
      showInAppToast('▶ Reprise synchronisée');
    } else if (data.event === 'pause' || (data.type === 'sync' && data.paused === true)) {
      v.pause();
      showInAppToast('⏸ Pause synchronisée');
    }
    setTimeout(() => { wpIsSyncing = false; }, 300);
  } else if (data.type === 'chat') {
    appendWpChatMessage(data.user || 'Ami', data.text || '');
  }
}

function broadcastWpEvent(event, time) {
  if (!wpRoomCode || wpConnections.length === 0 || wpIsSyncing) return;
  const payload = {
    type: 'event',
    event: event,
    time: time
  };
  wpConnections.forEach(conn => {
    if (conn.open) conn.send(payload);
  });
}

function toggleWpChat() {
  const box = document.getElementById('inAppWpChatBox');
  if (!box) return;
  box.style.display = (box.style.display === 'none' ? 'flex' : 'none');
}

function sendWpMessage() {
  const inp = document.getElementById('inAppWpInput');
  if (!inp) return;
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  
  appendWpChatMessage('Moi', text);
  const payload = { type: 'chat', user: 'Ami', text: text };
  wpConnections.forEach(conn => {
    if (conn.open) conn.send(payload);
  });
}

function appendWpChatMessage(user, text) {
  const box = document.getElementById('inAppWpChatBox');
  const msgs = document.getElementById('inAppWpMessages');
  if (box) box.style.display = 'flex';
  if (msgs) {
    const div = document.createElement('div');
    div.style.background = 'rgba(255,255,255,0.06)';
    div.style.padding = '4px 8px';
    div.style.borderRadius = '6px';
    div.style.wordBreak = 'break-word';
    div.innerHTML = `<b style="color:${user === 'Moi' ? '#4ade80' : '#38bdf8'}; font-size:0.75rem;">${user} :</b> <span style="color:#f4f4f5;">${escapeHtml(text)}</span>`;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }
  showInAppToast(`<strong>${escapeHtml(user)} :</strong> ${text.length > 25 ? text.substring(0,25) + '...' : text}`);
}

// --- Gestionnaire d'Add-ons ---
async function openAddonsModal() {
  const modal = document.getElementById('addonsModal');
  if (modal) modal.style.display = 'flex';
  await loadAddonsList();
}

function closeAddonsModal() {
  const modal = document.getElementById('addonsModal');
  if (modal) modal.style.display = 'none';
}

async function loadAddonsList() {
  const container = document.getElementById('addonsListContainer');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--dim); font-size:0.8rem; text-align:center; padding:20px;">Chargement des add-ons...</div>';

  try {
    const data = await api('/api/addons');
    const addons = data.addons || [];
    if (addons.length === 0) {
      container.innerHTML = '<div style="color:var(--muted); font-size:0.82rem; text-align:center; padding:30px;">Aucun add-on installé. Cliquez sur "+ Installer un Add-on" pour en ajouter.</div>';
      return;
    }

    container.innerHTML = addons.map(addon => {
      const isEnabled = Boolean(addon.enabled);
      const isBuiltin = Boolean(addon.builtin);
      return `
        <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 14px; display:flex; justify-content:space-between; align-items:center; gap:12px;">
          <div style="flex:1;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:0.92rem; font-weight:700; color:var(--text);">${escapeHtml(addon.name || addon.id)}</span>
              <span class="badge" style="font-size:0.68rem; background:rgba(255,255,255,0.06);">${escapeHtml(addon.version || '1.0.0')}</span>
              ${isBuiltin ? `<span class="badge" style="font-size:0.68rem; background:rgba(56,189,248,0.15); color:#38bdf8;">Officiel</span>` : ''}
              <span class="badge" style="font-size:0.68rem; background:rgba(74,222,128,0.15); color:#4ade80;">${escapeHtml(addon.type || 'scraper')}</span>
            </div>
            <p style="color:var(--muted); font-size:0.77rem; margin:4px 0 0; line-height:1.4;">${escapeHtml(addon.description || 'Add-on de recherche')}</p>
            ${addon.author ? `<span style="font-size:0.7rem; color:var(--dim); margin-top:2px; display:block;">Par ${escapeHtml(addon.author)}</span>` : ''}
          </div>
          <div style="display:flex; align-items:center; gap:10px;">
            <label style="position:relative; display:inline-block; width:38px; height:20px; cursor:pointer;">
              <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleAddonSwitch('${addon.id}', this.checked)" style="opacity:0; width:0; height:0;">
              <span style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background:${isEnabled ? '#4ade80' : 'rgba(255,255,255,0.15)'}; border-radius:20px; transition:.2s;"></span>
              <span style="position:absolute; content:''; height:14px; width:14px; left:${isEnabled ? '20px' : '3px'}; bottom:3px; background:white; border-radius:50%; transition:.2s;"></span>
            </label>
          </div>
        </div>
      `;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div style="color:#ef4444; font-size:0.8rem; text-align:center; padding:20px;">Erreur : ${e.message}</div>`;
  }
}

async function toggleAddonSwitch(addonId, enabled) {
  try {
    await api('/api/addons/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: addonId, enabled: enabled})
    });
    await loadAddonsList();
  } catch(e) {
    alert('Erreur activation add-on : ' + e.message);
    await loadAddonsList();
  }
}

async function uploadAddonFile(input) {
  if (!input || !input.files || input.files.length === 0) return;
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const content = e.target.result;
      const base64 = btoa(new Uint8Array(content).reduce((data, byte) => data + String.fromCharCode(byte), ''));
      await api('/api/addons/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename: file.name, data: base64})
      });
      alert(`Add-on [ ${file.name} ] installé avec succès !`);
      await loadAddonsList();
    } catch(err) {
      alert("Erreur lors de l'installation de l'add-on : " + err.message);
    } finally {
      input.value = '';
    }
  };
  reader.readAsArrayBuffer(file);
}

// --- Animation Japonaise (Intégré dans le catalogue unifié KINO) ---

// --- Collections Communauté & Letterboxd Curated ---
let currentCommunityLists = [];

async function loadCommunityTab() {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem; grid-column:1/-1; text-align:center; padding:30px;">Chargement des tendances communautaires...</p>';

  try {
    const data = await api('/api/community/curated');
    let curated = data.collections || [];

    // Ajouter également les listes Letterboxd personnalisées déjà importées par l'utilisateur
    try {
      const customData = await api('/api/letterboxd/custom-lists');
      const customLists = (customData.lists || []).map(l => ({
        id: l.id,
        title: l.title || 'Ma liste Letterboxd',
        badge: 'IMPORTÉ',
        author: l.user ? `@${l.user}` : 'Vous',
        description: l.description || `${l.items ? l.items.length : 0} films importés depuis Letterboxd.`,
        count: l.items ? l.items.length : 0,
        items: l.items || [],
      }));
      curated = [...customLists, ...curated];
    } catch(err) {}

    currentCommunityLists = curated;
    renderCommunityListsGrid(currentCommunityLists);
  } catch(e) {
    grid.innerHTML = `<p style="color:#ef4444; font-size:0.84rem; grid-column:1/-1; text-align:center; padding:30px;">Erreur : ${e.message}</p>`;
  }
}

function renderCommunityListsGrid(lists) {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  if (!lists || lists.length === 0) {
    grid.innerHTML = '<p style="color:var(--muted); font-size:0.84rem; grid-column:1/-1; text-align:center; padding:40px;">Aucune sélection disponible.</p>';
    return;
  }

  grid.innerHTML = lists.map(col => {
    return `
      <div style="background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; display:flex; flex-direction:column; justify-content:space-between; gap:12px; cursor:pointer; transition:transform 0.15s, border-color 0.15s;" onmouseover="this.style.borderColor='rgba(255,255,255,0.25)'; this.style.transform='translateY(-2px)';" onmouseout="this.style.borderColor='var(--border)'; this.style.transform='translateY(0)';" onclick="openCommunityList('${col.id}')">
        <div>
          <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
            <span class="badge" style="font-size:0.68rem; font-weight:700; background:rgba(255,255,255,0.08); color:var(--text); letter-spacing:0.5px;">${escapeHtml(col.badge || 'COLLECTION')}</span>
            <span class="badge" style="font-size:0.7rem; background:rgba(0,224,84,0.12); color:#00e054; border-color:rgba(0,224,84,0.25);">${col.count || (col.items ? col.items.length : 0)} films</span>
          </div>
          <h4 style="font-size:1.02rem; font-weight:700; margin:0 0 6px 0; color:#fafafa;">${escapeHtml(col.title)}</h4>
          <p style="color:var(--muted); font-size:0.78rem; line-height:1.4; margin:0;">${escapeHtml(col.description)}</p>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(255,255,255,0.06); padding-top:10px; margin-top:4px;">
          <span style="font-size:0.72rem; color:var(--dim);">Par ${escapeHtml(col.author || 'Communauté')}</span>
          <span style="font-size:0.75rem; color:#4ade80; font-weight:600;">Découvrir →</span>
        </div>
      </div>
    `;
  }).join('');
}

function openCommunityList(listId) {
  const col = currentCommunityLists.find(c => c.id === listId);
  if (!col) return;
  const activeSec = document.getElementById('communityActiveListSection');
  const titleEl = document.getElementById('communityActiveListTitle');
  const descEl = document.getElementById('communityActiveListDesc');
  const grid = document.getElementById('communityActiveListGrid');

  if (titleEl) titleEl.textContent = col.title;
  if (descEl) descEl.textContent = col.description;
  if (activeSec) {
    activeSec.style.display = 'block';
    activeSec.scrollIntoView({ behavior: 'smooth' });
  }

  if (grid) {
    grid.innerHTML = (col.items || []).map(m => {
      const inWl = isInWatchlist(m.id);
      const rating = m.imdbRating ? `★ ${m.imdbRating}` : '';
      return `
        <div class="card" onclick="selectMedia({id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})">
          <div class="card-poster-wrap">
            <img src="${m.poster || 'data:image/svg+xml,<svg xmlns=\"http://www.w3.org/2000/svg\"/>'}" alt="${escapeHtml(m.name)}" loading="lazy">
            ${rating ? `<span class="card-badge">${rating}</span>` : ''}
            <button class="card-wl-btn ${inWl ? 'in-list' : ''}" data-wl-id="${m.id}" onclick="toggleWatchlist(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${inWl ? 'Retirer' : 'Ajouter'}">${inWl ? '✓' : '+'}</button>
          </div>
          <div class="card-info">
            <div class="card-title">${escapeHtml(m.name)}</div>
            <div class="card-meta"><span>${m.year || ''}</span></div>
          </div>
        </div>
      `;
    }).join('');
  }
}

function closeCommunityActiveList() {
  const activeSec = document.getElementById('communityActiveListSection');
  if (activeSec) activeSec.style.display = 'none';
}

async function importCustomLetterboxdFromInput() {
  const inp = document.getElementById('communityLetterboxdInput');
  const val = inp ? inp.value.trim() : '';
  if (!val) {
    alert("Veuillez coller l'URL d'une liste Letterboxd.");
    return;
  }
  openLetterboxdModal();
  switchLbxModalTab('list');
  const listInp = document.getElementById('lbxListInput');
  if (listInp) listInp.value = val;
  importLetterboxdList();
}

function searchByPerson(name, mtype) {
  if (!name) return;
  const targetType = (mtype === 'series') ? 'series' : 'movie';
  document.getElementById('searchType').value = targetType;
  toggleSearchMode();
  const inp = document.getElementById('searchInput');
  inp.value = name;
  runSearch();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

async function toggleWatchedItem(mediaObj) {
  if (!mediaObj || !mediaObj.id) return;
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(mediaObj)
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  if (activeTab === 'watched' || activeTab === 'watchlist') {
    renderActiveListTab();
  } else if (catalogItems.length && (activeTab === 'movies' || activeTab === 'series')) {
    renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
  }
  if (currentMedia && currentMedia.id === mediaObj.id) {
    selectMedia(currentMedia);
  }
}

async function toggleWatchedSeriesEp(seasonNum, epNum) {
  if (!currentMedia || !currentMedia.id) return;
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: currentMedia.id,
      name: currentMedia.name,
      type: 'series',
      year: currentMedia.year || '',
      poster: currentMedia.poster || '',
      season: seasonNum,
      episode: epNum
    })
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  renderDetailEpisodes(seasonNum);
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.isNext ? `▶ Épisode suivant (${epCode})` : `▶ Lancer ${epCode}`;
  }
}

async function toggleWatchedWholeSeason(customSeason = null) {
  if (!currentMedia || !currentMedia.id) return;
  const s = Number(customSeason || (document.getElementById('seasonSelect') ? document.getElementById('seasonSelect').value : 1) || 1);
  const eps = seriesMetaVideos.filter(v => v.season === s).map(v => Number(v.episode || v.number || 0)).filter(n => n > 0);
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: currentMedia.id,
      name: currentMedia.name,
      type: 'series',
      year: currentMedia.year || '',
      poster: currentMedia.poster || '',
      season: s,
      season_all: true,
      episodes: eps
    })
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  renderDetailEpisodes(s);
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.isNext ? `▶ Épisode suivant (${epCode})` : `▶ Lancer ${epCode}`;
  }
}

// --- Glisser-Déposer (Drag & Drop) de fichiers .torrent et liens Magnet ---
async function handleTorrentFileSelect(files) {
  if (!files || !files.length) return;
  const file = files[0];
  const box = document.getElementById('debridResultPanel');
  if (box) {
    box.style.display = 'block';
    box.scrollIntoView({ behavior: 'smooth' });
    box.innerHTML = `<h3>Lecture du fichier ${file.name}...</h3><p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Extraction de l'empreinte BitTorrent et débridage Cloud...</p>`;
  }
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    const b64 = btoa(binary);
    const res = await api('/api/upload-torrent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename: file.name, data_b64: b64 })
    });
    if (res && res.magnet) {
      currentMedia = { id: '', name: res.name || file.name, type: 'movie', year: '', poster: '' };
      await debridMagnet(res.magnet);
    }
  } catch (e) {
    if (box) box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur fichier .torrent : ${e.message}</p>`;
  }
}

let dragDepthCounter = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepthCounter++;
  const ov = document.getElementById('dropZoneOverlay');
  if (ov) ov.classList.add('active');
});
window.addEventListener('dragover', (e) => {
  e.preventDefault();
});
window.addEventListener('dragleave', (e) => {
  e.preventDefault();
  dragDepthCounter = Math.max(0, dragDepthCounter - 1);
  if (dragDepthCounter === 0) {
    const ov = document.getElementById('dropZoneOverlay');
    if (ov) ov.classList.remove('active');
  }
});
window.addEventListener('drop', async (e) => {
  e.preventDefault();
  dragDepthCounter = 0;
  const ov = document.getElementById('dropZoneOverlay');
  if (ov) ov.classList.remove('active');
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    const f = e.dataTransfer.files[0];
    if (f.name.toLowerCase().endsWith('.torrent')) {
      await handleTorrentFileSelect(e.dataTransfer.files);
      return;
    }
  }
  const text = e.dataTransfer ? (e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text/uri-list') || '').trim() : '';
  if (text && text.startsWith('magnet:?')) {
    await debridMagnet(text);
  }
});

let searchSuggestItems = [];
let searchSuggestIndex = -1;

function toggleShortcutsModal() {
  const m = document.getElementById('shortcutsModal');
  if (!m) return;
  const isOpen = m.style.display === 'flex';
  m.style.display = isOpen ? 'none' : 'flex';
}

function toggleSearchMode() {
  const mode = document.getElementById('searchType').value;
  const inp = document.getElementById('searchInput');
  hideSearchDropdown();
  if (mode === 'magnet') inp.placeholder = 'Coller un lien magnet:?xt=urn:btih:...';
  else if (mode === 'raw') inp.placeholder = 'Recherche par mots-clés...';
  else inp.placeholder = 'Rechercher un film ou une série... (⌘K)';
}

function onSearchInput() {
  clearTimeout(searchDebounceTimer);
  const mode = document.getElementById('searchType').value;
  const q = document.getElementById('searchInput').value.trim();
  if (mode === 'magnet' || mode === 'raw') {
    hideSearchDropdown();
    return;
  }
  if (!q) {
    hideSearchDropdown();
    if (activeTab === 'movies' || activeTab === 'series') {
      switchTab(activeTab);
    }
    return;
  }
  if (q.length < 2) {
    hideSearchDropdown();
    return;
  }
  // Interroger immédiatement SQLite FTS5 pour autocomplétion instantanée
  fetchSearchSuggestions(q, mode);
}

async function fetchSearchSuggestions(q, mode) {
  try {
    const res = await api(`/api/search-suggest?q=${encodeURIComponent(q)}&type=${encodeURIComponent(mode)}`);
    const results = (res && res.results) || [];
    renderSearchDropdown(results, q);
  } catch (e) {
    hideSearchDropdown();
  }
}

function renderSearchDropdown(items, query) {
  const dd = document.getElementById('searchDropdown');
  if (!dd) return;
  searchSuggestItems = items || [];
  searchSuggestIndex = -1;
  if (!items.length) {
    dd.style.display = 'none';
    dd.innerHTML = '';
    return;
  }
  dd.innerHTML = items.map((it, idx) => {
    const poster = it.poster || '';
    const typeLabel = it.type === 'series' ? 'Série' : 'Film';
    const rating = it.imdbRating ? `★ ${it.imdbRating}` : '';
    return `
      <div class="search-suggest-item" id="suggestItem_${idx}" onclick="selectSearchSuggest(${idx})">
        <img class="search-suggest-poster" src="${poster}" alt="" onerror="this.style.opacity=0.08">
        <div class="search-suggest-info">
          <div class="search-suggest-title">${it.name}</div>
          <div class="search-suggest-meta">
            <span class="badge" style="font-size:0.68rem; padding:1px 5px;">${typeLabel}</span>
            ${it.year ? `<span>${it.year}</span>` : ''}
            ${rating ? `<span style="color:#fafafa; font-weight:600;">${rating}</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
  dd.style.display = 'block';
}

function hideSearchDropdown() {
  const dd = document.getElementById('searchDropdown');
  if (dd) {
    dd.style.display = 'none';
    dd.innerHTML = '';
  }
  searchSuggestItems = [];
  searchSuggestIndex = -1;
}

function selectSearchSuggest(idx) {
  const it = searchSuggestItems[idx];
  hideSearchDropdown();
  if (!it) return;
  const inp = document.getElementById('searchInput');
  if (inp) inp.value = it.name;
  selectMedia(it);
}

function onSearchKeyDown(e) {
  const dd = document.getElementById('searchDropdown');
  const isDropdownVisible = dd && dd.style.display === 'block';

  if (isDropdownVisible) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      searchSuggestIndex = (searchSuggestIndex + 1) % searchSuggestItems.length;
      updateSelectedSuggest();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      searchSuggestIndex = (searchSuggestIndex - 1 + searchSuggestItems.length) % searchSuggestItems.length;
      updateSelectedSuggest();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (searchSuggestIndex >= 0 && searchSuggestIndex < searchSuggestItems.length) {
        selectSearchSuggest(searchSuggestIndex);
      } else {
        hideSearchDropdown();
        runSearch();
      }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      hideSearchDropdown();
      return;
    }
  } else if (e.key === 'Enter') {
    hideSearchDropdown();
    runSearch();
  }
}

function updateSelectedSuggest() {
  document.querySelectorAll('.search-suggest-item').forEach((el, idx) => {
    el.classList.toggle('selected', idx === searchSuggestIndex);
  });
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-box')) {
    hideSearchDropdown();
  }
});

async function runSearch() {
  clearTimeout(searchDebounceTimer);
  const mode = document.getElementById('searchType').value;
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;

  if (mode === 'magnet') {
    debridMagnet(q);
    return;
  }

  document.getElementById('detailPanel').style.display = 'none';
  document.getElementById('rdCloudPanel').style.display = 'none';
  document.getElementById('homeResumeSection').style.display = 'none';
  const hs = document.getElementById('heroSpotlight');
  if (hs) hs.style.display = 'none';
  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const lm = document.getElementById('loadMoreWrap');
  if (gf) gf.style.display = 'none';
  if (sw) sw.style.display = 'none';
  if (lm) lm.style.display = 'none';

  if (mode === 'raw') {
    document.getElementById('postersGrid').innerHTML = '';
    document.getElementById('seriesControls').style.display = 'none';
    loadTorrents({q, title: q});
    return;
  }

  document.getElementById('postersGrid').style.display = 'grid';
  document.getElementById('catalogHeader').style.display = 'flex';
  document.getElementById('catalogTitle').textContent = `Résultats pour "${q}"`;
  const grid = document.getElementById('postersGrid');
  grid.innerHTML = '<p style="color:var(--dim); font-size:0.85rem;">Recherche en cours...</p>';
  document.getElementById('torrentsPanel').style.display = 'none';

  try {
    const data = await api(`/api/search?type=${mode}&q=${encodeURIComponent(q)}`);
    if (!data.metas || !data.metas.length) {
      grid.innerHTML = '';
      loadTorrents({q, title: q});
      return;
    }
    renderPosterCards(data.metas.slice(0, 36), mode);
  } catch (e) {
    grid.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
  }
}

async function oneClickCard(ev, btn, media) {
  ev.stopPropagation();
  currentMedia = media;
  const histItem = userHistory.find(h => h.id === media.id);
  const isSeries = Boolean(media.type === 'series' || (media.is_anime && media.type !== 'movie'));
  if (isSeries) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const s = targetEp.season;
    const e = targetEp.episode;
    await oneClickPlay({
      imdb_id: media.id,
      type: 'series',
      season: s,
      episode: e,
      title: `${media.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
      name: media.name,
      poster: media.poster || '',
      year: media.year || ''
    }, btn);
  } else {
    await oneClickPlay({
      imdb_id: media.id,
      type: 'movie',
      title: `${media.name}${media.year ? ' (' + media.year + ')' : ''}`,
      name: media.name,
      poster: media.poster || '',
      year: media.year || ''
    }, btn);
  }
}

async function oneClickSeriesEpisode(btn, customSeason = null, customEpisode = null) {
  if (!currentMedia) return;
  const s = customSeason || document.getElementById('seasonSelect').value || 1;
  const e = customEpisode || document.getElementById('episodeSelect').value || 1;
  await oneClickPlay({
    imdb_id: currentMedia.id,
    type: 'series',
    season: s,
    episode: e,
    title: `${currentMedia.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
    name: currentMedia.name,
    poster: currentMedia.poster || '',
    year: currentMedia.year || ''
  }, btn);
}

async function oneClickPlay(params, btn) {
  if (btn && btn.disabled) return;
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }

  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});
  box.innerHTML = `
    <h3>${params.title}</h3>
    <p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Lancement du flux...</p>
  `;

  try {
    const res = await api('/api/one-click-play', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params)
    });
    if (btn) btn.innerHTML = 'Lancé';
    const plCount = (res.mpv && res.mpv.playlist_count) ? res.mpv.playlist_count : 1;
    renderDebridState(res.debrid, params.season, params.episode, res.chosen_torrent, plCount);
    refreshUserLists();

    if ((window.kinoPlayerMode || 'kino') === 'integrated' && res.stream_url) {
      const mediaInfo = currentMedia || {
        id: params.imdb_id,
        name: params.name || params.title,
        type: params.type || 'movie',
        season: params.season,
        episode: params.episode,
        poster: params.poster,
        year: params.year
      };
      openInAppPlayer(res.stream_url, params.title || (res.file && res.file.filename) || 'KINO', res.playlist, mediaInfo, res.resume_sec || 0);
    }
  } catch (e) {
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }, 2500);
    }
  }
}

async function selectMedia(media) {
  currentMedia = media;
  const detail = document.getElementById('detailPanel');
  detail.style.display = 'block';
  detail.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement de la fiche...</p>';
  detail.scrollIntoView({behavior: 'smooth'});

  const sc = document.getElementById('seriesControls');
  let meta = {};
  const queryType = (media.type === 'movie') ? 'movie' : (media.type === 'series' ? 'series' : (media.is_anime ? 'anime' : (media.type || 'movie')));
  try {
    const res = await api(`/api/meta?type=${encodeURIComponent(queryType)}&imdb_id=${encodeURIComponent(media.id)}`);
    meta = res.meta || {};
    if (meta.type && !media.type) media.type = meta.type;
  } catch (e) {
    console.warn(e);
  }
  const isSeries = Boolean(media.type === 'series' || meta.type === 'series' || (meta.videos && meta.videos.length > 0));
  if (isSeries) {
    media.type = 'series';
    currentMedia.type = 'series';
  } else if (meta.type === 'movie') {
    media.type = 'movie';
    currentMedia.type = 'movie';
  }

  const poster = meta.poster || media.poster || '';
  currentMedia.poster = poster;
  const year = meta.releaseInfo || meta.year || media.year || '';
  const rating = meta.imdbRating || media.imdbRating || '';
  const runtime = meta.runtime || '';
  const rawGenres = (meta.genres || meta.genre || []).slice(0, 5);
  const genres = (meta.genres_fr || rawGenres).slice(0, 5);
  const desc = meta.description_fr || meta.description || 'Aucun synopsis disponible.';
  const castArr = (meta.cast || []).slice(0, 7);
  const dirArr = Array.isArray(meta.director) ? meta.director : (meta.director ? [meta.director] : []);
  const mtypeSafe = JSON.stringify(media.type || 'movie').replace(/'/g, "&#39;");
  const castLinks = castArr.map(p => {
    const pJs = JSON.stringify(p).replace(/'/g, "&#39;");
    return `<span onclick='searchByPerson(${pJs}, ${mtypeSafe})' title="Voir les titres avec ${p}" style="cursor:pointer; color:var(--text); text-decoration:underline; text-decoration-color:var(--border-hover); text-underline-offset:3px;">${p}</span>`;
  }).join(', ');
  const dirLinks = dirArr.map(d => {
    const dJs = JSON.stringify(d).replace(/'/g, "&#39;");
    return `<span onclick='searchByPerson(${dJs}, ${mtypeSafe})' title="Voir les réalisations de ${d}" style="cursor:pointer; color:var(--text); text-decoration:underline; text-decoration-color:var(--border-hover); text-underline-offset:3px;">${d}</span>`;
  }).join(', ');
  const country = meta.country || '';
  const awards = meta.awards || '';
  const trailerId = (meta.trailers && meta.trailers[0] && meta.trailers[0].source) ? meta.trailers[0].source : '';
  const inList = isInWatchlist(media.id);
  const mediaPayload = JSON.stringify({id: media.id, name: media.name, type: media.type, year: String(year), poster, imdbRating: String(rating)}).replace(/'/g, "&#39;");
  const safeNameJs = JSON.stringify(meta.name || media.name).replace(/'/g, "&#39;");
  const safeYearJs = JSON.stringify(String(year)).replace(/'/g, "&#39;");

  const histItem = userHistory.find(h => h.id === media.id);
  const isMovieDone = Boolean(histItem && media.type !== 'series' && (histItem.completed || Number(histItem.progress_pct || 0) >= 85));
  const watchedEpsList = (histItem && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes : [];
  const latestAired = meta.latest_aired || null;
  const latestAiredUnwatched = Boolean(latestAired && latestAired.code && !watchedEpsList.includes(latestAired.code));

  let targetSeason = 1;
  let targetEpisode = 1;
  let playBtnLabel = 'Play';
  if (media.type === 'series') {
    const targetEp = getSeriesTargetEpisode(histItem);
    targetSeason = targetEp.season;
    targetEpisode = targetEp.episode;
    const epCode = `S${String(targetSeason).padStart(2,'0')}E${String(targetEpisode).padStart(2,'0')}`;
    playBtnLabel = targetEp.isNext ? `▶ Épisode suivant (${epCode})` : `▶ Lancer ${epCode}`;
  } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85) {
    playBtnLabel = `▶ Reprendre (${Math.round(histItem.progress_pct)}%)`;
  } else if (isMovieDone) {
    playBtnLabel = '▶ Revoir le film';
  }

  let episodesHtml = '';
  if (media.type === 'series') {
    seriesMetaVideos = (meta.videos || []).filter(v => v.season > 0);
    const seasons = [...new Set(seriesMetaVideos.map(v => v.season))].sort((a,b) => a - b);
    if (seasons.length) {
      if (!seasons.includes(targetSeason)) targetSeason = seasons[0];
      episodesHtml = `
        <div style="margin-top:18px; border-top:1px solid var(--border); padding-top:14px;">
          <div style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px;">
            <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
              <span style="font-size:0.8rem; color:var(--muted); margin-right:6px;">Saisons :</span>
              ${seasons.map(s => `<button class="chip ${s===targetSeason?'active':''}" data-season-chip="${s}" onclick="selectDetailSeason(${s})">Saison ${s}</button>`).join('')}
            </div>
            <button class="btn btn-secondary" id="detailSeasonAllWatchedBtn" style="padding:5px 11px; font-size:0.75rem;" onclick="toggleWatchedWholeSeason()">✓ Marquer la saison comme vue</button>
          </div>
          <div id="detailEpisodesList" class="episodes-grid"></div>
        </div>
      `;
    }
  }

  const similarList = Array.isArray(meta.similar) ? meta.similar : [];
  const similarHtml = similarList.length ? `
    <div style="margin-top:18px; border-top:1px solid var(--border); padding-top:14px;">
      <div style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); margin-bottom:10px;">Vous aimerez aussi</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(115px, 1fr)); gap:10px;">
        ${similarList.map(sm => {
          const smPayload = JSON.stringify(sm).replace(/'/g, "&#39;");
          return `
            <div class="poster-card" onclick='selectMedia(${smPayload})'>
              <img src="${sm.poster || ''}" alt="${sm.name}" loading="lazy" onerror="this.style.opacity=0.08">
              <div class="poster-info" style="padding:7px;">
                <div class="poster-title" style="font-size:0.76rem;">${sm.name}</div>
                <div class="poster-year" style="font-size:0.69rem;">${sm.year || ''}${sm.imdbRating ? ' • ★ ' + sm.imdbRating : ''}</div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  ` : '';

  const latestAiredBanner = (media.type === 'series' && latestAired) ? `
    <div style="display:inline-flex; align-items:center; gap:8px; background:var(--surface-2); border:1px solid ${latestAiredUnwatched ? '#fafafa' : 'var(--border)'}; padding:4px 10px; border-radius:5px; font-size:0.75rem; color:${latestAiredUnwatched ? '#fafafa' : 'var(--muted)'}; margin-top:2px; width:fit-content;">
      <span>${latestAiredUnwatched ? '● Dernier épisode diffusé (non vu) :' : '✓ Dernier épisode diffusé :'} <strong>${latestAired.code}</strong> — ${latestAired.title} (${latestAired.released})</span>
      ${latestAiredUnwatched ? `<button class="btn" style="padding:2px 8px; font-size:0.7rem;" onclick="oneClickSeriesEpisode(this, ${latestAired.season}, ${latestAired.episode})">▶ Play</button>` : ''}
    </div>
  ` : '';

  detail.innerHTML = `
    <div class="detail-layout">
      <img class="detail-poster" src="${poster}" alt="${media.name}" onerror="this.style.opacity=0.08">
      <div class="detail-body">
        <div class="detail-title">${meta.name || media.name}</div>
        <div class="detail-sub">
          ${year ? `<span>${year}</span>` : ''}
          ${runtime ? `<span>• ${runtime}</span>` : ''}
          ${rating ? `<span style="color:#fafafa; font-weight:600;">• ★ ${rating} IMDb</span>` : ''}
          ${country ? `<span>• ${country.split(',').slice(0,2).join(', ')}</span>` : ''}
          ${genres.map((g, idx) => {
            const rawG = rawGenres[idx] || g;
            const rawGJs = JSON.stringify(rawG).replace(/'/g, "&#39;");
            return `<span class="badge" style="cursor:pointer;" title="Filtrer le catalogue par ${g}" onclick='selectGenre(${rawGJs}, document.querySelector("#genreFilters .chip[data-genre=\\"" + ${rawGJs} + "\\"]"))'>${g}</span>`;
          }).join('')}
        </div>
        ${latestAiredBanner}
        <div class="detail-desc">${desc}</div>
        <div class="detail-credits">
          ${dirLinks ? `<div><strong>Réalisation :</strong> ${dirLinks}</div>` : ''}
          ${castLinks ? `<div><strong>Distribution :</strong> ${castLinks}</div>` : ''}
          ${awards ? `<div style="color:var(--dim); margin-top:2px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.45 1-1 1H8v2h8v-2h-1c-.55 0-1-.45-1-1v-2.34c3.27-.47 5.73-3.23 6-6.66H4c.27 3.43 2.73 6.19 6 6.66z"/></svg>${awards}</div>` : ''}
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center;">
          <button class="btn" id="detailMainPlayBtn" onclick='oneClickCard(event, this, ${mediaPayload})'>${playBtnLabel}</button>
          <button class="btn btn-secondary" onclick='openTrailerModal(${JSON.stringify(trailerId)}, ${safeNameJs}, ${safeYearJs}, "vf")'><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>Bande-annonce</button>
          <button class="btn btn-secondary" id="detailWlBtn" onclick='toggleWatchlist(event, ${mediaPayload})'>${inList ? '✓ Dans ma liste' : '+ Ma Liste'}</button>
          ${media.type !== 'series' ? `<button class="btn btn-secondary" style="${isMovieDone ? 'border-color:var(--text); color:var(--text);' : ''}" onclick='toggleWatchedItem(${mediaPayload})'>${isMovieDone ? '✓ Vu' : '✓ Marquer comme vu'}</button>` : ''}
          ${media.id && String(media.id).startsWith('tt') ? `<a class="btn btn-secondary" href="https://www.imdb.com/title/${encodeURIComponent(media.id)}/" target="_blank" style="padding:7px 11px; font-size:0.76rem;" title="Voir la fiche sur IMDb">IMDb ↗</a>` : ''}
          ${media.id && String(media.id).startsWith('tt') && media.type !== 'series' ? `<a class="btn btn-secondary" href="https://letterboxd.com/imdb/${encodeURIComponent(media.id)}/" target="_blank" style="padding:7px 11px; font-size:0.76rem; border-color:rgba(0,224,84,0.35);" title="Voir sur Letterboxd">Letterboxd ↗</a>` : ''}
        </div>
      </div>
    </div>
    ${episodesHtml}
    ${similarHtml}
  `;

  if (media.type === 'series') {
    sc.style.display = 'flex';
    const sSel = document.getElementById('seasonSelect');
    const seasons = [...new Set(seriesMetaVideos.map(v => v.season))].sort((a,b) => a - b);
    if (seasons.length) {
      sSel.innerHTML = seasons.map(s => `<option value="${s}">Saison ${s}</option>`).join('');
      sSel.value = String(targetSeason);
      onSeasonChange(false);
      const eSel = document.getElementById('episodeSelect');
      if (eSel && [...eSel.options].some(o => Number(o.value) === targetEpisode)) {
        eSel.value = String(targetEpisode);
      }
      renderDetailEpisodes(targetSeason);
    } else {
      sSel.innerHTML = '<option value="1">Saison 1</option>';
      onSeasonChange(false);
    }
    reloadSeriesEpisode(false);
  } else {
    sc.style.display = 'none';
    loadTorrents({
      imdb_id: media.id,
      type: 'movie',
      title: `${media.name}${year ? ' (' + year + ')' : ''}`,
      runtime: media.runtime || (meta && meta.runtime) || ''
    }, false);
  }
}

function selectDetailSeason(seasonNum) {
  document.querySelectorAll('[data-season-chip]').forEach(c => {
    c.classList.toggle('active', Number(c.getAttribute('data-season-chip')) === seasonNum);
  });
  const sSel = document.getElementById('seasonSelect');
  if (sSel) {
    sSel.value = String(seasonNum);
    onSeasonChange(false);
  }
  renderDetailEpisodes(seasonNum);
}

function renderDetailEpisodes(seasonNum) {
  const container = document.getElementById('detailEpisodesList');
  if (!container) return;
  const eps = seriesMetaVideos.filter(v => v.season === seasonNum).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  if (!eps.length) {
    container.innerHTML = '<p style="color:var(--dim); font-size:0.82rem;">Aucun détail d\'épisode disponible.</p>';
    return;
  }
  const histItem = currentMedia ? userHistory.find(h => h.id === currentMedia.id) : null;
  const watchedList = (histItem && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes : [];
  const epPosMap = (histItem && histItem.ep_positions) ? histItem.ep_positions : {};

  const seasonCodes = eps.map(v => `S${String(seasonNum).padStart(2,'0')}E${String(v.episode || v.number || 1).padStart(2,'0')}`);
  const isAllSeasonWatched = seasonCodes.length > 0 && seasonCodes.every(c => watchedList.includes(c));
  for (const btnId of ['detailSeasonAllWatchedBtn', 'markSeasonWatchedBtn']) {
    const sBtn = document.getElementById(btnId);
    if (sBtn) {
      sBtn.textContent = isAllSeasonWatched ? `✓ Saison ${seasonNum} vue` : `✓ Marquer saison ${seasonNum} vue`;
      sBtn.style.borderColor = isAllSeasonWatched ? '#fafafa' : '';
      sBtn.style.color = isAllSeasonWatched ? '#fafafa' : '';
    }
  }

  container.innerHTML = eps.map(v => {
    const epNum = v.episode || v.number || 1;
    const epTitle = v.name || v.title || `Épisode ${epNum}`;
    const epOverview = v.overview || v.description || '';
    const thumb = v.thumbnail || (currentMedia && currentMedia.poster) || '';
    const code = `S${String(seasonNum).padStart(2,'0')}E${String(epNum).padStart(2,'0')}`;
    const isWatched = watchedList.includes(code);
    const epProg = epPosMap[code] ? Number(epPosMap[code].pct || 0) : 0;
    const watchedBadge = isWatched
      ? '<span class="badge" style="border-color:var(--text); color:var(--text);">✓ Vu</span>'
      : (epProg > 1 ? `<span class="badge">${Math.round(epProg)}%</span>` : '');
    const progBar = epProg > 0 ? `
      <div style="height:2px; background:var(--surface-2); border-radius:2px; overflow:hidden; margin-top:5px; max-width:220px;">
        <div style="height:100%; width:${Math.min(100, epProg)}%; background:var(--text);"></div>
      </div>
    ` : '';
    return `
      <div class="ep-card" style="${isWatched ? 'opacity:0.75;' : ''}">
        <img class="ep-thumb" src="${thumb}" alt="" loading="lazy" onerror="this.style.opacity=0.08">
        <div class="ep-info">
          <div class="ep-title"><span class="badge badge-hi">${code}</span> <span id="filler-badge-${seasonNum}-${epNum}"></span> ${watchedBadge}${epTitle}</div>
          ${epOverview ? `<div class="ep-desc">${epOverview}</div>` : ''}
          ${progBar}
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
          <button class="btn" style="padding:6px 11px; font-size:0.78rem;" onclick="oneClickSeriesEpisode(this, ${seasonNum}, ${epNum})">${epProg > 1 && epProg < 92 ? 'Reprendre' : 'Play'}</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.78rem;" onclick="pickSeriesEpisodeSources(${seasonNum}, ${epNum})">Sources</button>
          <button class="btn btn-secondary" style="padding:6px 9px; font-size:0.75rem; ${isWatched ? 'border-color:var(--text); color:var(--text);' : ''}" title="${isWatched ? 'Démarquer comme vu' : 'Marquer cet épisode comme vu'}" onclick="toggleWatchedSeriesEp(${seasonNum}, ${epNum})">${isWatched ? '✓ Vu' : '✓'}</button>
        </div>
      </div>
    `;
  }).join('');

  if (currentMedia && (currentMedia.type === 'series' || currentMedia.is_anime || activeTab === 'anime')) {
    const title = currentMedia.name || '';
    eps.forEach(v => {
      const epNum = v.episode || v.number || 1;
      api(`/api/anime/filler?title=${encodeURIComponent(title)}&episode=${epNum}`).then(res => {
        const badgeEl = document.getElementById(`filler-badge-${seasonNum}-${epNum}`);
        if (badgeEl && res && res.filler) {
          const f = res.filler;
          if (f.is_filler) {
            badgeEl.innerHTML = `<span class="badge" style="background:rgba(249,115,22,0.18); color:#f97316; border-color:rgba(249,115,22,0.4);" title="${f.type || 'Filler'}">Hors-Série</span>`;
          } else {
            badgeEl.innerHTML = `<span class="badge" style="background:rgba(74,222,128,0.18); color:#4ade80; border-color:rgba(74,222,128,0.4);" title="Manga Canon">Canon</span>`;
          }
        }
      }).catch(() => {});
    });
  }
}

function pickSeriesEpisodeSources(s, e) {
  document.getElementById('seasonSelect').value = String(s);
  onSeasonChange(false);
  document.getElementById('episodeSelect').value = String(e);
  reloadSeriesEpisode(true);
}

function onSeasonChange(triggerReload = true) {
  const s = parseInt(document.getElementById('seasonSelect').value || '1', 10);
  const eSel = document.getElementById('episodeSelect');
  const eps = seriesMetaVideos.filter(v => v.season === s).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  if (eps.length) {
    eSel.innerHTML = eps.map(v => {
      const epNum = v.episode || v.number;
      const epTitle = v.name || v.title || `Épisode ${epNum}`;
      return `<option value="${epNum}">E${String(epNum).padStart(2,'0')} — ${epTitle}</option>`;
    }).join('');
  } else {
    eSel.innerHTML = Array.from({length: 24}, (_, i) => `<option value="${i+1}">Épisode ${i+1}</option>`).join('');
  }
  if (triggerReload) reloadSeriesEpisode(false);
}

function reloadSeriesEpisode(scrollToTorrents = false) {
  if (!currentMedia) return;
  const s = document.getElementById('seasonSelect').value || 1;
  const e = document.getElementById('episodeSelect').value || 1;
  loadTorrents({
    imdb_id: currentMedia.id,
    type: 'series',
    season: s,
    episode: e,
    title: `${currentMedia.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`
  }, scrollToTorrents);
}

function extractTorrentSizeGb(t) {
  if (t.size_gb !== undefined && t.size_gb !== null && !isNaN(t.size_gb) && Number(t.size_gb) > 0) {
    return Number(t.size_gb);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mGb = text.match(/(\d+(?:\.\d+)?)\s*(?:GB|GiB)/i);
  if (mGb) return parseFloat(mGb[1]);
  const mMb = text.match(/(\d+(?:\.\d+)?)\s*(?:MB|MiB)/i);
  if (mMb) return parseFloat(mMb[1]) / 1024.0;
  return 0;
}

function extractTorrentSeeders(t) {
  if (t.seeders !== undefined && t.seeders !== null && !isNaN(t.seeders) && Number(t.seeders) > 0) {
    return Number(t.seeders);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mSeed = text.match(/(\d+)\s*(?:seeders?|seeds?|pairs?)/i) || text.match(/(?:👤|peers?|seeds?|seeders?)\s*(\d+)/i);
  if (mSeed) return parseInt(mSeed[1], 10);
  return 0;
}

async function loadTorrents(params, scroll = true) {
  const panel = document.getElementById('torrentsPanel');
  const list = document.getElementById('torrentsList');
  panel.style.display = 'block';
  document.getElementById('torrentsHeading').textContent = params.title;
  document.getElementById('torrentsSub').textContent = 'Recherche des sources...';
  list.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement...</p>';
  if (scroll) panel.scrollIntoView({behavior: 'smooth'});

  if (!params.runtime && currentMedia && currentMedia.runtime) {
    params.runtime = currentMedia.runtime;
  }

  const qs = new URLSearchParams(params).toString();
  try {
    const data = await api('/api/torrents?' + qs);
    allTorrents = data.torrents || [];
    renderTorrents();
  } catch (e) {
    list.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
  }
}

function setFilter(flt, el) {
  activeFilter = flt;
  document.querySelectorAll('.filters .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderTorrents();
}

function getFilteredTorrents() {
  const custom = document.getElementById('customFilter')?.value.trim().toLowerCase() || '';
  const words = custom ? custom.split(/\s+/).filter(Boolean) : [];
  const isMovie = !currentMedia || currentMedia.type !== 'series';
  const rm = currentMedia && currentMedia.runtime ? parseInt(currentMedia.runtime, 10) : 0;

  let filtered = allTorrents.filter(t => {
    const quals = t.qualities || [];
    const hay = (t.title + ' ' + t.meta + ' ' + t.source + ' ' + quals.join(' ') + ' ' + (t.langs || []).join(' ')).toLowerCase();

    // Filtre des torrents frauduleux / CAM réencodés trop légers pour leurs caractéristiques
    const size = extractTorrentSizeGb(t);
    const is4k = quals.includes('4K') || /\b(2160p|4k|uhd)\b/i.test(t.title);
    const is1080 = quals.includes('1080p') || /\b(1080p|fhd)\b/i.test(t.title);

    if (/\b(CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER|DVDSCREENER|WP|WORKPRINT)\b/i.test(t.title)) {
      return false;
    }

    if (isMovie && size > 0) {
      let min4k = 4.8;
      if (rm > 70) min4k = Math.max(4.8, (rm * 60 * 6.0) / (8 * 1024));
      if (is4k && size < min4k) return false;

      let min1080 = 0.75;
      if (rm > 70) min1080 = Math.max(0.75, (rm * 60 * 1.5) / (8 * 1024));
      if (is1080 && size < min1080) return false;

      if (size < 0.15) return false;
    } else if (!isMovie && size > 0) {
      if (is4k && size < 1.3) return false;
      if (is1080 && size < 0.22) return false;
      if (size < 0.08) return false;
    }

    if (activeFilter === 'fr') {
      if (!(t.langs && t.langs.length) && !/(multi|french|vff|vfq|vostfr|truefrench|\bfr\b)/i.test(hay)) return false;
    } else if (activeFilter === 'sdr') {
      if (quals.includes('HDR') || quals.includes('DV') || /\b(hdr|hdr10|dv|dovi|dolby[\s\.\-]*vision)\b/i.test(t.title || '')) return false;
    } else if (activeFilter === '4k') {
      if (!quals.includes('4K') && !/(2160p|4k|uhd)/i.test(hay)) return false;
    } else if (activeFilter === '1080p') {
      if (!quals.includes('1080p') && !/(1080p|fhd)/i.test(hay)) return false;
    } else if (activeFilter && !hay.includes(activeFilter)) {
      return false;
    }
    if (words.length && !words.every(w => hay.includes(w))) return false;
    return true;
  });

  const sortMode = document.getElementById('torrentSortSelect')?.value || 'default';
  if (sortMode === 'seeds') {
    filtered.sort((a, b) => extractTorrentSeeders(b) - extractTorrentSeeders(a));
  } else if (sortMode === 'size_desc') {
    filtered.sort((a, b) => extractTorrentSizeGb(b) - extractTorrentSizeGb(a));
  } else if (sortMode === 'size_asc') {
    filtered.sort((a, b) => {
      const sa = extractTorrentSizeGb(a);
      const sb = extractTorrentSizeGb(b);
      if (!sa && !sb) return 0;
      if (!sa) return 1;
      if (!sb) return -1;
      return sa - sb;
    });
  }

  return filtered;
}

function scoreTorrent(t) {
  let score = 0;
  const seeds = extractTorrentSeeders(t);
  score += Math.min(seeds * 2, 80);

  const isCached = (t.source && t.source.includes('+')) || (t.qualities && t.qualities.some(q => q.endsWith('+')));
  if (isCached) score += 150;

  const titleUpper = (t.title || '').toUpperCase();
  const langs = (t.langs || []).map(l => l.toUpperCase());
  if (langs.includes('MULTI') || titleUpper.includes('MULTI') || langs.includes('VFF') || langs.includes('VF') || titleUpper.includes('FRENCH')) {
    score += 60;
  } else if (langs.includes('VOSTFR') || titleUpper.includes('VOSTFR')) {
    score += 40;
  }

  if (t.qualities && (t.qualities.includes('1080p') || t.qualities.includes('4K'))) {
    score += 30;
  }

  return score;
}

function renderTorrents() {
  const list = document.getElementById('torrentsList');
  const filtered = getFilteredTorrents();

  const subEl = document.getElementById('torrentsSub');
  if (subEl) {
    subEl.textContent = `${filtered.length} source${filtered.length > 1 ? 's' : ''} disponible${filtered.length > 1 ? 's' : ''}${filtered.length !== allTorrents.length ? ` (sur ${allTorrents.length})` : ''}`;
  }

  if (!filtered.length) {
    list.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Aucun résultat pour ce filtre.</p>';
    return;
  }

  let bestIdx = -1;
  let maxScore = -1;
  filtered.forEach((t, i) => {
    const sc = scoreTorrent(t);
    if (sc > maxScore) {
      maxScore = sc;
      bestIdx = i;
    }
  });

  list.innerHTML = filtered.slice(0, 75).map((t, idx) => {
    const isBest = (idx === bestIdx && maxScore >= 120);
    const recBadge = isBest ? '<span class="badge badge-recommended" title="Équilibre optimal Seeders + Résolution + Langue">Recommandé</span>' : '';
    const qualBadges = (t.qualities || []).map(q => {
      const isHi = q.endsWith('+') || (q === 'SDR' && (window.kinoHdrMode || 'sdr_pref') === 'sdr_pref');
      const tip = q === 'DV' ? ' title="Dolby Vision (Tone-Mapping anti-noirs bouchés actif)"'
                : q === 'HDR' ? ' title="HDR10 (Tone-Mapping anti-noirs bouchés actif)"'
                : q === 'SDR' ? ' title="Standard Dynamic Range (Étalonnage lumineux standard)"' : '';
      return `<span class="badge ${isHi ? 'badge-hi' : ''}"${tip}>${q}</span>`;
    }).join('');
    const langBadges = (t.langs || []).map(l => `<span class="badge badge-hi">${l}</span>`).join('');
    return `
      <div class="torrent-item ${isBest ? 'is-recommended' : ''}">
        <div style="flex:1; min-width:260px;">
          <div class="torrent-title">
            ${recBadge}${qualBadges}${langBadges}${t.title}
          </div>
          <div class="torrent-meta">${t.source}${t.meta ? ' • ' + t.meta : ''}</div>
        </div>
        <div style="display:flex; gap:6px; align-items:center;">
          ${t.magnet ? `<button class="btn btn-secondary" style="padding:6px 10px; font-size:0.75rem;" onclick="copyTorrentMagnet(event, this, ${idx})" title="Copier le lien Magnet">Magnet</button>` : ''}
          <button class="btn ${isBest ? '' : 'btn-secondary'}" style="${isBest ? 'font-weight:600;' : ''}" onclick="debridFromIndex(${idx})">${isBest ? '▶ Lancer' : 'Sélectionner'}</button>
        </div>
      </div>
    `;
  }).join('');
}

async function copyTorrentMagnet(ev, btnEl, idx) {
  if (ev) ev.stopPropagation();
  const filtered = getFilteredTorrents();
  const item = filtered[idx];
  if (!item || !item.magnet) return;
  try {
    await navigator.clipboard.writeText(item.magnet);
    if (btnEl) {
      const orig = btnEl.textContent;
      btnEl.textContent = '✓ Copié';
      setTimeout(() => { btnEl.textContent = orig; }, 1400);
    }
  } catch (e) {}
}

async function debridFromIndex(startIdx) {
  const filtered = getFilteredTorrents();
  const candidates = filtered.slice(startIdx);
  await debridCandidates(candidates);
}

async function debridMagnet(magnet) {
  await debridCandidates([{title: 'Magnet direct', magnet}]);
}

async function debridCandidates(candidates) {
  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});

  const season = currentMedia && currentMedia.type === 'series' ? document.getElementById('seasonSelect').value : null;
  const episode = currentMedia && currentMedia.type === 'series' ? document.getElementById('episodeSelect').value : null;

  for (let i = 0; i < candidates.length; i++) {
    const item = candidates[i];
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Débridage en cours (${i + 1}/${candidates.length}) : <strong style="color:var(--text);">${item.title || 'Magnet'}</strong>...</p>`;
    try {
      const res = await api('/api/debrid', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({magnet: item.magnet, resolve_url: item.resolve_url || '', season, episode})
      });
      renderDebridState(res, season, episode);
      return;
    } catch (e) {
      if (i + 1 < candidates.length && (e.message.includes('451') || e.message.includes('infringing_file') || e.message.includes('refusé'))) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Source #${i + 1} indisponible (451), passage à la suivante...</p>`;
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
      return;
    }
  }
}

function renderDebridState(res, season, episode, chosenTorrentTitle = '', playlistCount = 1) {
  const box = document.getElementById('debridResultPanel');
  if (!res.ready) {
    box.innerHTML = `
      <h3>Mise en cache Real-Debrid (${res.progress}%)</h3>
      <p style="color:var(--muted); font-size:0.82rem; margin-top:4px;">Statut : ${res.status} • Vitesse : ${res.speed} • Pairs : ${res.seeders || 0}</p>
      <div class="progress-bar"><div class="progress-fill" style="width:${res.progress}%"></div></div>
    `;
    setTimeout(async () => {
      try {
        const next = await api('/api/debrid-status', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({torrent_id: res.torrent_id, season, episode})
        });
        renderDebridState(next, season, episode, chosenTorrentTitle, playlistCount);
      } catch (e) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
      }
    }, 2500);
    return;
  }

  window.lastDebridFiles = res.files || [];

  const subBanner = chosenTorrentTitle
    ? `<p style="color:var(--muted); font-size:0.8rem; margin-bottom:6px;">Lecture : ${chosenTorrentTitle}</p>`
    : '';
  const playlistNote = (playlistCount > 1 || (currentMedia && currentMedia.type === 'series'))
    ? `<p style="color:var(--dim); font-size:0.77rem; margin-bottom:10px;">Playlist active${playlistCount > 1 ? ` (${playlistCount} épisodes)` : ''} — Touche <code style="color:var(--text); background:var(--surface-2); padding:1px 5px; border-radius:3px; border:1px solid var(--border);">&gt;</code> pour l'épisode suivant</p>`
    : '';

  box.innerHTML = `
    <h3 style="margin-bottom:6px;">Fichier prêt</h3>
    ${subBanner}
    ${playlistNote}
    <div style="display:flex; flex-direction:column; gap:8px;">
      ${res.files.map(f => `
        <div class="torrent-item" style="${f.is_target_ep ? 'border-color: var(--border-hover);' : ''}">
          <div style="flex:1; min-width:240px;">
            <div class="torrent-title">
              ${f.is_target_ep ? '<span class="badge badge-hi">Épisode</span>' : ''}
              ${f.filename}
            </div>
            <div class="torrent-meta">${f.filesize}</div>
          </div>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn" onclick='openMpv(this, ${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Play</button>
            <button class="btn btn-secondary" onclick='startPcDownload(${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Télécharger</button>
            <a class="btn btn-secondary" href="${f.download}" target="_blank">Lien direct</a>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

async function startPcDownload(url, filename) {
  await api('/api/download', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url, filename})
  });
  pollDownloads();
}

async function cancelDownload(dl_id) {
  await api('/api/download-cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dl_id})
  });
  pollDownloads();
}

async function openMpv(btn, url, filename) {
  if (btn && btn.disabled) return;
  const origText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }
  const s = currentMedia && currentMedia.type === 'series' ? document.getElementById('seasonSelect').value : null;
  const e = currentMedia && currentMedia.type === 'series' ? document.getElementById('episodeSelect').value : null;
  let packFiles = null;
  if (window.lastDebridFiles && window.lastDebridFiles.length > 1 && window.lastDebridFiles.some(f => f.download === url)) {
    const sorted = [...window.lastDebridFiles].sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
    const idx = sorted.findIndex(f => f.download === url);
    if (idx >= 0) packFiles = sorted.slice(idx);
  }

  if ((window.kinoPlayerMode || 'kino') === 'integrated') {
    const playlist = packFiles && packFiles.length > 1
      ? packFiles.map(f => ({ title: f.filename, url: f.download }))
      : [{ title: filename, url }];
    openInAppPlayer(url, filename, playlist, currentMedia);
    if (btn) {
      btn.innerHTML = 'Lancé';
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
    return;
  }

  try {
    await api('/api/mpv', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        filename,
        pack_files: packFiles,
        media: currentMedia ? {
          id: currentMedia.id,
          name: currentMedia.name,
          type: currentMedia.type,
          year: currentMedia.year || '',
          poster: currentMedia.poster || '',
          season: s,
          episode: e
        } : null
      })
    });
    if (btn) btn.innerHTML = 'Lancé';
    refreshUserLists();
  } catch (err) {
    alert(err.message);
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
  }
}

(function initMacNativeWindow() {
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
  const hasWkBridge = Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge);
  if (isMac && (hasWkBridge || window.pywebview)) {
    document.body.classList.add('is-mac-native');
  }
  window.addEventListener('pywebviewready', () => {
    if (isMac) document.body.classList.add('is-mac-native');
  });

  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (!document.body.classList.contains('is-mac-native')) return;
    const dragRegion = e.target.closest('.pywebview-drag-region');
    if (!dragRegion) return;
    if (e.target.closest('.no-drag, button, input, select, textarea, a, label')) return;
    try {
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge) {
        window.webkit.messageHandlers.jsBridge.postMessage(JSON.stringify({
          funcName: 'start_window_drag',
          params: [],
          id: 'mac_drag'
        }));
        return;
      }
      if (window.pywebview && window.pywebview.api && window.pywebview.api.start_window_drag) {
        window.pywebview.api.start_window_drag();
      }
    } catch (err) {}
  });
})();

async function windowAction(action) {
  if (action === 'maximize' && document.body.classList.contains('is-mac-native') && window.event && window.event.type === 'dblclick') {
    return;
  }
  try {
    if (window.pywebview && window.pywebview.api) {
      if (action === 'minimize' && window.pywebview.api.minimize) {
        window.pywebview.api.minimize();
        return;
      }
      if (action === 'maximize' && window.pywebview.api.toggle_maximize) {
        window.pywebview.api.toggle_maximize();
        setTimeout(syncWindowState, 80);
        return;
      }
      if (action === 'close' && window.pywebview.api.close) {
        window.pywebview.api.close();
        return;
      }
    }
  } catch (e) {}

  await api('/api/window/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action})
  }).catch(() => {});
}

function initResizeHandles() {
  const handles = document.querySelectorAll('.win-resize-handle');
  const isWin = /Win/i.test(navigator.platform || navigator.userAgent);
  handles.forEach(h => {
    h.addEventListener('mousedown', async (e) => {
      if (e.button !== 0) return;
      const dir = h.dataset.dir;
      if (!window.pywebview || !window.pywebview.api) return;
      e.preventDefault();
      e.stopPropagation();

      if (window.pywebview.api.get_state && window.pywebview.api.set_bounds) {
        const st = await window.pywebview.api.get_state();
        if (!st || st.maximized) return;
        const startX = e.screenX;
        const startY = e.screenY;
        const origX = st.x;
        const origY = st.y;
        const origW = st.width;
        const origH = st.height;
        const minW = 640;
        const minH = 420;
        let rafPending = false;

        const onMove = (ev) => {
          if (rafPending) return;
          rafPending = true;
          requestAnimationFrame(() => {
            rafPending = false;
            const dx = ev.screenX - startX;
            const dy = ev.screenY - startY;
            let nx = origX;
            let ny = origY;
            let nw = origW;
            let nh = origH;

            if (dir.includes('right')) {
              nw = Math.max(minW, origW + dx);
            }
            if (dir.includes('left')) {
              const candW = origW - dx;
              if (candW >= minW) {
                nw = candW;
                nx = origX + dx;
              } else {
                nw = minW;
                nx = origX + (origW - minW);
              }
            }
            if (dir.includes('bottom')) {
              nh = Math.max(minH, origH + dy);
            }
            if (dir.includes('top')) {
              const candH = origH - dy;
              if (candH >= minH) {
                nh = candH;
                ny = origY + dy;
              } else {
                nh = minH;
                ny = origY + (origH - minH);
              }
            }
            window.pywebview.api.set_bounds(nx, ny, nw, nh);
          });
        };

        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };

        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }
    });
  });
}

async function syncWindowState() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) {
      const state = await window.pywebview.api.get_state();
      if (state && state.maximized) {
        document.body.classList.add('is-maximized');
      } else {
        document.body.classList.remove('is-maximized');
      }
    }
  } catch (e) {}
}

window.addEventListener('resize', syncWindowState);
setInterval(syncWindowState, 1500);

const SVG_ICONS = {
  play: '<svg class="svg-icon" viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  pause: '<svg class="svg-icon" viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
  volHigh: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
  volLow: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
  volMute: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>'
};

let inAppPlaylist = [];
let inAppPlaylistIndex = 0;
let inAppCurrentMedia = null;
let inAppCurrentUrl = '';
let inAppCurrentTitle = '';
let inAppIdleTimeout = null;
let inAppFallbackTimer = null;
let inAppIsDraggingVol = false;

function formatTime(sec) {
  if (!sec || isNaN(sec) || sec < 0) return '00:00';
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const remSec = s % 60;
  if (m < 60) {
    return `${String(m).padStart(2, '0')}:${String(remSec).padStart(2, '0')}`;
  }
  const h = Math.floor(m / 60);
  const remMin = m % 60;
  return `${String(h).padStart(2, '0')}:${String(remMin).padStart(2, '0')}:${String(remSec).padStart(2, '0')}`;
}

function resetInAppIdleTimer() {
  const overlay = document.getElementById('inAppPlayerOverlay');
  if (!overlay || !overlay.classList.contains('active')) return;
  overlay.classList.remove('idle');
  clearTimeout(inAppIdleTimeout);
  inAppIdleTimeout = setTimeout(() => {
    const video = document.getElementById('inAppVideo');
    if (video && !video.paused && !inAppIsDraggingVol) {
      overlay.classList.add('idle');
    }
  }, 2500);
}

function showInAppToast(msg, duration = 3000) {
  const toast = document.getElementById('inAppStatusToast');
  if (!toast) return;
  toast.innerHTML = msg;
  toast.style.display = 'block';
  if (duration > 0) {
    setTimeout(() => {
      toast.style.display = 'none';
    }, duration);
  }
}

function hideInAppToast() {
  const toast = document.getElementById('inAppStatusToast');
  if (toast) toast.style.display = 'none';
}

function updatePlayPauseBtn(isPlaying) {
  const btn = document.getElementById('inAppPlayPauseBtn');
  if (btn) {
    btn.innerHTML = isPlaying ? SVG_ICONS.pause : SVG_ICONS.play;
  }
}

function setInAppVolume(vol, unmute = true) {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const clamped = Math.max(0, Math.min(1, vol));
  video.volume = clamped;
  if (unmute && video.muted && clamped > 0) {
    video.muted = false;
    video._userMuted = false;
  }
  try {
    localStorage.setItem('kino_inapp_volume', String(clamped));
    localStorage.setItem('kino_inapp_muted', String(video.muted));
  } catch (e) {}
  updateInAppVolUI();
}

function updateInAppVolUI() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const fill = document.getElementById('inAppVolFill');
  const txt = document.getElementById('inAppVolText');
  const icon = document.getElementById('inAppVolIcon');

  const isMuted = video.muted || video.volume === 0;
  const displayPct = isMuted ? 0 : Math.round(video.volume * 100);

  if (fill) fill.style.width = displayPct + '%';
  if (txt) txt.textContent = displayPct + '%';
  if (icon) {
    if (isMuted) {
      icon.innerHTML = SVG_ICONS.volMute;
    } else if (video.volume < 0.5) {
      icon.innerHTML = SVG_ICONS.volLow;
    } else {
      icon.innerHTML = SVG_ICONS.volHigh;
    }
  }
}

function toggleInAppMute() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  if (video.muted) {
    video.muted = false;
    video._userMuted = false;
    if (video.volume === 0) video.volume = 0.5;
  } else {
    video.muted = true;
    video._userMuted = true;
  }
  try {
    localStorage.setItem('kino_inapp_volume', String(video.volume));
    localStorage.setItem('kino_inapp_muted', String(video.muted));
  } catch (e) {}
  updateInAppVolUI();
}

function fixInAppAudio() {
  const video = document.getElementById('inAppVideo');
  if (!video || !inAppCurrentUrl) return;
  const curSec = Math.floor(video.currentTime || 0);
  const remuxUrl = `/api/remux?url=${encodeURIComponent(inAppCurrentUrl)}&ss=${curSec}`;
  showInAppToast('<strong>🔊 Conversion Audio AAC compatible en cours...</strong><br><span style="font-size:0.78rem; color:var(--muted);">Remuxing audio instantané stéréo sans perte vidéo</span>', 2800);
  video.src = remuxUrl;
  video.load();
  video.muted = false;
  video._userMuted = false;
  if (!video.volume || video.volume < 0.1) video.volume = 1.0;
  video.play().catch(() => {});
  updateInAppVolUI();
  const btn = document.getElementById('inAppAudioFixBtn');
  if (btn) btn.innerHTML = '✓ Son AAC Actif';
}

function handleVolScrub(e) {
  const wrap = document.getElementById('inAppVolSliderWrap');
  if (!wrap) return;
  const rect = wrap.getBoundingClientRect();
  const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
  const ratio = clickX / rect.width;
  setInAppVolume(ratio, true);
  resetInAppIdleTimer();
}

function onVolMouseDown(e) {
  e.preventDefault();
  inAppIsDraggingVol = true;
  handleVolScrub(e);

  const onMouseMove = (ev) => {
    if (!inAppIsDraggingVol) return;
    handleVolScrub(ev);
  };
  const onMouseUp = () => {
    inAppIsDraggingVol = false;
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    resetInAppIdleTimer();
  };

  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
}

function inAppWheelVolume(delta) {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const step = delta > 0 ? -0.05 : 0.05;
  setInAppVolume(video.volume + step, true);
  resetInAppIdleTimer();
}

const INAPP_CLARITY_PRESETS = [
  { id: 'normal', label: 'Clarté : Normal', filter: 'none', note: 'Étalonnage standard' },
  { id: 'boost1', label: 'Clarté : Ombres +', filter: 'url(#kinoShadowBoost1) brightness(1.08) contrast(1.03) saturate(1.04)', note: 'Débouche les scènes sombres (Gamma +25%)' },
  { id: 'boost2', label: 'Clarté : Nuit ++', filter: 'url(#kinoShadowBoost2) brightness(1.16) contrast(1.05) saturate(1.07)', note: 'Anti-noirs bouchés intensif (Gamma +45%)' },
  { id: 'boost3', label: 'Clarté : HDR Max', filter: 'url(#kinoShadowBoost3) brightness(1.26) contrast(1.07) saturate(1.10)', note: 'Correction maximale flux HDR / Dolby Vision très sombres' }
];
let inAppClarityIdx = 0;

function applyInAppClarity(idx, showToast = false) {
  inAppClarityIdx = ((idx % INAPP_CLARITY_PRESETS.length) + INAPP_CLARITY_PRESETS.length) % INAPP_CLARITY_PRESETS.length;
  const preset = INAPP_CLARITY_PRESETS[inAppClarityIdx];
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppClarityBtn');
  if (video) {
    video.style.filter = preset.filter;
  }
  if (btn) {
    btn.textContent = preset.label;
    btn.style.borderColor = inAppClarityIdx > 0 ? '#fafafa' : '';
  }
  if (showToast) {
    showInAppToast(`<strong>${preset.label}</strong><br><span style="font-size:0.78rem; color:var(--muted);">${preset.note}</span>`, 1800);
  }
}

function cycleInAppClarity() {
  applyInAppClarity(inAppClarityIdx + 1, true);
  resetInAppIdleTimer();
}

// --- Sous-titres OpenSubtitles v3 (FR / EN) ---
let inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', url: '' }];
let inAppActiveSubIdx = 0;
let inAppActiveCues = [];
let inAppSubDelaySec = 0;
const inAppSubCuesCache = {};

async function loadInAppSubtitlesForCurrentMedia() {
  inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', url: '' }];
  inAppActiveSubIdx = 0;
  inAppActiveCues = [];
  inAppSubDelaySec = 0;
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
  const btn = document.getElementById('inAppSubsBtn');
  if (btn) { btn.textContent = 'CC : Off'; btn.style.borderColor = ''; }

  if (!inAppCurrentMedia || !inAppCurrentMedia.id) return;
  const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : 1);
  const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : 1);
  const mtype = inAppCurrentMedia.type || 'movie';
  try {
    const res = await api(`/api/subtitles?imdb_id=${encodeURIComponent(inAppCurrentMedia.id)}&type=${encodeURIComponent(mtype)}&season=${s || 1}&episode=${e || 1}`);
    const subs = (res && res.subtitles) || [];
    const frList = subs.filter(x => x.lang === 'fr').slice(0, 2);
    const enList = subs.filter(x => x.lang === 'en').slice(0, 2);
    frList.forEach((item, idx) => {
      inAppAvailableSubs.push({
        label: frList.length > 1 ? `CC : Français #${idx + 1}` : 'CC : Français',
        lang: 'fr',
        url: item.url
      });
    });
    enList.forEach((item, idx) => {
      inAppAvailableSubs.push({
        label: enList.length > 1 ? `CC : English #${idx + 1}` : 'CC : English',
        lang: 'en',
        url: item.url
      });
    });
    // Si le flux est VOSTFR ou VO et qu'une piste FR existe, on peut pré-charger la piste FR
    if (/\b(vostfr|vost|multi|vo|eng)\b/i.test(inAppCurrentTitle || '') && frList.length > 0) {
      // Préchargement silencieux en cache
      api(`/api/subtitle-cues?url=${encodeURIComponent(frList[0].url)}`).then(d => {
        if (d && d.cues) inAppSubCuesCache[frList[0].url] = d.cues;
      }).catch(() => {});
    }
  } catch (err) {}
}

async function cycleInAppSubtitles() {
  resetInAppIdleTimer();
  if (inAppAvailableSubs.length <= 1) {
    showInAppToast(`<strong>Sous-titres</strong><br><span style="font-size:0.78rem; color:var(--muted);">Aucun sous-titre OpenSubtitles trouvé pour ce titre</span>`, 1800);
    return;
  }
  inAppActiveSubIdx = (inAppActiveSubIdx + 1) % inAppAvailableSubs.length;
  const sub = inAppAvailableSubs[inAppActiveSubIdx];
  const btn = document.getElementById('inAppSubsBtn');
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (btn) {
    btn.textContent = sub.label;
    btn.style.borderColor = sub.lang !== 'off' ? '#fafafa' : '';
  }
  if (sub.lang === 'off' || !sub.url) {
    inAppActiveCues = [];
    if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
    showInAppToast(`<strong>Sous-titres désactivés</strong>`, 1400);
    return;
  }
  showInAppToast(`<strong>${sub.label}</strong><br><span style="font-size:0.78rem; color:var(--muted);">Chargement OpenSubtitles... (G / H pour décaler ±0.5s)</span>`, 1800);
  if (inAppSubCuesCache[sub.url]) {
    inAppActiveCues = inAppSubCuesCache[sub.url];
    const v = document.getElementById('inAppVideo');
    if (v) updateInAppSubtitleOverlay(v.currentTime);
    return;
  }
  try {
    const data = await api(`/api/subtitle-cues?url=${encodeURIComponent(sub.url)}`);
    if (data && Array.isArray(data.cues)) {
      inAppSubCuesCache[sub.url] = data.cues;
      inAppActiveCues = data.cues;
      const v = document.getElementById('inAppVideo');
      if (v) updateInAppSubtitleOverlay(v.currentTime);
    }
  } catch (err) {
    showInAppToast(`<strong>Erreur sous-titres</strong><br><span style="font-size:0.78rem; color:var(--muted);">${err.message}</span>`, 1800);
  }
}

let inAppSubSizeIdx = 1;
const SUB_SIZES = [
  { label: 'A-', size: '1.1rem', name: 'Petite' },
  { label: 'A', size: '1.32rem', name: 'Standard' },
  { label: 'A+', size: '1.6rem', name: 'Grande' },
  { label: 'A++', size: '1.9rem', name: 'Très grande' }
];

function cycleInAppSubSize() {
  resetInAppIdleTimer();
  inAppSubSizeIdx = (inAppSubSizeIdx + 1) % SUB_SIZES.length;
  const cfg = SUB_SIZES[inAppSubSizeIdx];
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) subOverlay.style.fontSize = cfg.size;
  const btn = document.getElementById('inAppSubSizeBtn');
  if (btn) btn.textContent = cfg.label;
  showInAppToast(`<strong>Taille sous-titres : ${cfg.name}</strong>`, 1400);
}

function adjustInAppSubDelay(deltaSec) {
  if (!inAppActiveCues.length) return;
  inAppSubDelaySec = Math.round((inAppSubDelaySec + deltaSec) * 100) / 100;
  const sign = inAppSubDelaySec >= 0 ? '+' : '';
  showInAppToast(`<strong>Décalage sous-titres : ${sign}${inAppSubDelaySec}s</strong> (Z / X)`, 1400);
  const v = document.getElementById('inAppVideo');
  if (v) updateInAppSubtitleOverlay(v.currentTime);
}

function updateInAppSubtitleOverlay(currentTime) {
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (!subOverlay) return;
  if (!inAppActiveCues || !inAppActiveCues.length) {
    subOverlay.style.display = 'none';
    return;
  }
  const t = currentTime - inAppSubDelaySec;
  const active = [];
  for (let i = 0; i < inAppActiveCues.length; i++) {
    const c = inAppActiveCues[i];
    if (t >= c.start && t <= c.end) {
      active.push(c.text);
    } else if (c.start > t + 2) {
      break;
    }
  }
  if (!active.length) {
    subOverlay.style.display = 'none';
    subOverlay.innerHTML = '';
    return;
  }
  const escaped = active.join('\n').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  subOverlay.innerHTML = `<span class="sub-box">${escaped}</span>`;
  subOverlay.style.display = 'block';
}

// --- Mode Boost Voix / Audio Nuit ---
function applyInAppAudioUI(mode, showToast = false) {
  window.kinoAudioMode = mode || 'voice_boost';
  const btn = document.getElementById('inAppAudioBtn');
  const isBoost = window.kinoAudioMode === 'voice_boost';
  if (btn) {
    btn.textContent = isBoost ? 'Voix : Boost' : 'Voix : Normal';
    btn.style.borderColor = isBoost ? '#fafafa' : '';
  }
  const sel = document.getElementById('cfgAudioMode');
  if (sel) sel.value = window.kinoAudioMode;
  if (showToast) {
    showInAppToast(
      isBoost
        ? `<strong>Boost Voix / Audio Nuit activé</strong><br><span style="font-size:0.78rem; color:var(--muted);">Dialogues rehaussés & explosions adoucies (normalisation dynamique)</span>`
        : `<strong>Audio : Standard</strong><br><span style="font-size:0.78rem; color:var(--muted);">Plage dynamique cinéma originale sans compression</span>`,
      1900
    );
  }
}

function cycleInAppAudioBoost() {
  resetInAppIdleTimer();
  const nextMode = (window.kinoAudioMode || 'voice_boost') === 'voice_boost' ? 'normal' : 'voice_boost';
  applyInAppAudioUI(nextMode, true);
  api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ audio_mode: nextMode })
  }).catch(() => {});
}

// --- Vitesse de lecture (0.75x à 2.0x) & Capture d'écran ---
const INAPP_SPEED_PRESETS = [0.75, 1.0, 1.25, 1.5, 2.0];
let inAppSpeedIdx = 1;
let inAppSkipIntroDismissed = false;
let inAppNextEpCancelled = false;

function applyInAppSpeed(idx, showToast = false) {
  inAppSpeedIdx = ((idx % INAPP_SPEED_PRESETS.length) + INAPP_SPEED_PRESETS.length) % INAPP_SPEED_PRESETS.length;
  const spd = INAPP_SPEED_PRESETS[inAppSpeedIdx];
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppSpeedBtn');
  if (video) {
    video.playbackRate = spd;
    if ('preservesPitch' in video) video.preservesPitch = true;
    else if ('webkitPreservesPitch' in video) video.webkitPreservesPitch = true;
  }
  if (btn) {
    btn.textContent = `${spd}x`;
    btn.style.borderColor = spd !== 1.0 ? '#fafafa' : '';
  }
  if (showToast) {
    showInAppToast(`<strong>Vitesse : ${spd}x</strong>`, 1300);
  }
}

function cycleInAppSpeed(step = 1) {
  resetInAppIdleTimer();
  applyInAppSpeed(inAppSpeedIdx + step, true);
}

async function captureInAppScreenshot() {
  resetInAppIdleTimer();
  const video = document.getElementById('inAppVideo');
  if (!video || !video.videoWidth || !video.videoHeight) {
    showInAppToast(`<strong>Capture impossible</strong><br><span style="font-size:0.78rem; color:var(--muted);">Aucune image vidéo active</span>`, 1600);
    return;
  }
  try {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/png');
    const timeStr = formatTime(video.currentTime).replace(/:/g, 'm') + 's';
    const res = await api('/api/save-screenshot', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: inAppCurrentTitle || 'KINO',
        time_str: timeStr,
        data_url: dataUrl
      })
    });
    if (res && res.filename) {
      showInAppToast(`<strong>Capture enregistrée</strong><br><span style="font-size:0.78rem; color:var(--muted);">${res.filename}</span>`, 2200);
    }
  } catch (e) {
    showInAppToast(`<strong>Capture protégée (CORS)</strong><br><span style="font-size:0.78rem; color:var(--muted);">Utilisez ⇧⌘4 ou le Moteur KINO (touche S dans IINA)</span>`, 2200);
  }
}

function skipInAppIntro() {
  resetInAppIdleTimer();
  inAppSkipIntroDismissed = true;
  const card = document.getElementById('inAppSkipIntroCard');
  if (card) card.style.display = 'none';
  inAppSeekRel(85);
  showInAppToast(`<strong>Intro passée (+85s)</strong>`, 1400);
}

function dismissSkipIntro() {
  inAppSkipIntroDismissed = true;
  const card = document.getElementById('inAppSkipIntroCard');
  if (card) card.style.display = 'none';
}

function cancelNextEpAuto() {
  inAppNextEpCancelled = true;
  const card = document.getElementById('inAppNextEpCard');
  if (card) card.style.display = 'none';
}

// --- Mode Picture-in-Picture (PiP — Fenêtre flottante macOS) ---
async function toggleInAppPiP() {
  resetInAppIdleTimer();
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppPipBtn');
  if (!video) return;
  try {
    if (video.webkitSupportsPresentationMode && typeof video.webkitSetPresentationMode === 'function') {
      const nextMode = video.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture';
      video.webkitSetPresentationMode(nextMode);
      if (btn) btn.style.borderColor = nextMode === 'picture-in-picture' ? '#fafafa' : '';
      showInAppToast(nextMode === 'picture-in-picture' ? `<strong>⧉ Picture-in-Picture activé</strong>` : `<strong>⧉ Retour à la fenêtre KINO</strong>`, 1400);
      return;
    }
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture();
      if (btn) btn.style.borderColor = '';
    } else if (video.requestPictureInPicture) {
      await video.requestPictureInPicture();
      if (btn) btn.style.borderColor = '#fafafa';
      showInAppToast(`<strong>⧉ Picture-in-Picture activé</strong>`, 1400);
    }
  } catch (e) {
    showInAppToast(`<strong>⧉ PiP indisponible sur ce flux</strong><br><span style="font-size:0.78rem; color:var(--muted);">Utilisez « Moteur KINO » pour le mode flottant natif</span>`, 1800);
  }
}

function openInAppPlayer(streamUrl, title, playlist = null, media = null, resumeSec = 0) {
  const overlay = document.getElementById('inAppPlayerOverlay');
  const video = document.getElementById('inAppVideo');
  const titleEl = document.getElementById('inAppTitle');

  clearTimeout(inAppFallbackTimer);
  hideInAppToast();

  inAppCurrentUrl = streamUrl;
  inAppCurrentTitle = title;
  inAppCurrentMedia = media || currentMedia;
  inAppSkipIntroDismissed = false;
  inAppNextEpCancelled = false;
  traktScrobbledStarted = false;
  traktScrobbledStop = false;
  startRemotePolling();
  const skipCard = document.getElementById('inAppSkipIntroCard');
  if (skipCard) skipCard.style.display = 'none';
  const nextCard = document.getElementById('inAppNextEpCard');
  if (nextCard) nextCard.style.display = 'none';

  if (playlist && Array.isArray(playlist) && playlist.length > 0) {
    inAppPlaylist = playlist;
    const foundIdx = playlist.findIndex(p => p.url === streamUrl);
    inAppPlaylistIndex = foundIdx >= 0 ? foundIdx : 0;
  } else {
    inAppPlaylist = [{ title: title, url: streamUrl }];
    inAppPlaylistIndex = 0;
  }

  titleEl.textContent = title || 'Lecture';
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
  resetInAppIdleTimer();

  const isHdrStream = /\b(hdr|hdr10|dv|dovi|dolby[\s\.\-]*vision)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  if ((window.kinoHdrMode || 'sdr_pref') === 'hdr_boost') {
    applyInAppClarity(2, false);
  } else if (isHdrStream && (window.kinoHdrMode || 'sdr_pref') !== 'hdr_native' && inAppClarityIdx === 0) {
    applyInAppClarity(1, true);
  } else {
    applyInAppClarity(inAppClarityIdx, false);
  }

  // Détection des formats exigeants (HEVC / 10-bit / DTS) et proposition MPV / Audio AAC
  const isHeavyCodec = /\b(hevc|h\.?265|10bit|hdr|dv|dovi|dts|truehd|remux)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  const hasTrickyAudio = /\b(dts|dts-hd|truehd|atmos|eac3|ac3|ddp|dd\+|flac|5\.1|7\.1)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  const mpvBtn = document.getElementById('inAppMpvSuggestBtn');
  if (mpvBtn) {
    mpvBtn.style.display = isHeavyCodec ? 'inline-flex' : 'none';
  }
  const audioFixBtn = document.getElementById('inAppAudioFixBtn');
  if (audioFixBtn) {
    audioFixBtn.style.display = hasTrickyAudio ? 'inline-flex' : 'none';
    audioFixBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><line x1="1" y1="1" x2="23" y2="23"/></svg>Son AAC';
  }
  if (hasTrickyAudio) {
    setTimeout(() => {
      showInAppToast(`<strong>Piste audio multi-canal / cinéma détectée</strong><br><span style="font-size:0.78rem; color:var(--muted);">Pas de son ? Cliquez sur <strong>Son AAC</strong> en haut ou <strong>Basculer MPV</strong></span>`, 3200);
    }, 1400);
  } else if (isHeavyCodec) {
    setTimeout(() => {
      showInAppToast(`<strong>Format 4K HDR / DTS détecté</strong><br><span style="font-size:0.78rem; color:var(--muted);">Touche E ou bouton dédié pour basculer sur MPV sans perte</span>`, 2800);
    }, 1200);
  }

  // Déverrouillage forcé du son & restauration du volume sauvegardé
  video.muted = false;
  video._userMuted = false;
  let savedVol = null;
  try {
    savedVol = localStorage.getItem('kino_inapp_volume');
    if (localStorage.getItem('kino_inapp_muted') === 'true') {
      video.muted = true;
      video._userMuted = true;
    }
  } catch(e) {}
  video.volume = (savedVol !== null && !isNaN(savedVol) && parseFloat(savedVol) > 0) ? Math.max(0.05, parseFloat(savedVol)) : 1.0;

  applyInAppAudioUI(window.kinoAudioMode || 'voice_boost', false);
  applyInAppSpeed(1, false);
  loadInAppSubtitlesForCurrentMedia();

  updateInAppVolUI();
  updatePlayPauseBtn(false);

  let playbackStarted = false;

  const onPlaying = () => {
    playbackStarted = true;
    clearTimeout(inAppFallbackTimer);
    hideInAppToast();
    if (resumeSec > 15 && video.duration && resumeSec < video.duration * 0.95) {
      video.currentTime = resumeSec;
    }
  };

  const onFallbackRequired = (reason) => {
    if (playbackStarted) return;
    clearTimeout(inAppFallbackTimer);
    showInAppToast(`Flux 4K/MKV non décodable dans le navigateur.<br><strong>Lancement instantané du moteur KINO...</strong>`, 2600);
    setTimeout(() => {
      switchToExternalPlayer();
    }, 900);
  };

  video.removeEventListener('playing', video._onPlayingHandler || (() => {}));
  video.removeEventListener('error', video._onErrorHandler || (() => {}));

  video._onPlayingHandler = onPlaying;
  video._onErrorHandler = () => onFallbackRequired('error');

  video.addEventListener('playing', video._onPlayingHandler, { once: true });
  video.addEventListener('error', video._onErrorHandler, { once: true });

  video.src = streamUrl;
  video.load();

  const playPromise = video.play();
  if (playPromise !== undefined) {
    playPromise.catch(e => {
      if (e && e.name === 'NotAllowedError') {
        video.muted = true;
        updateInAppVolUI();
        video.play().then(() => {
          showInAppToast(`<strong>🔊 Cliquez sur l'écran ou appuyez sur M pour activer le son</strong>`, 3500);
        }).catch(() => {});
      } else if (video.error || video.readyState === 0) {
        onFallbackRequired('play_reject');
      }
    });
  }

  inAppFallbackTimer = setTimeout(() => {
    if (!playbackStarted && (video.paused || video.readyState === 0 || video.currentTime === 0)) {
      onFallbackRequired('timeout');
    }
  }, 4500);

  if (inAppCurrentMedia && inAppCurrentMedia.id) {
    const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
    const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
    api('/api/history-record', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        media: {
          id: inAppCurrentMedia.id,
          name: inAppCurrentMedia.name || title,
          type: inAppCurrentMedia.type || 'movie',
          year: inAppCurrentMedia.year || '',
          poster: inAppCurrentMedia.poster || '',
          season: s,
          episode: e,
          filename: title
        }
      })
    }).then(refreshUserLists).catch(() => {});
  }
}

function closeInAppPlayer() {
  clearTimeout(inAppFallbackTimer);
  const overlay = document.getElementById('inAppPlayerOverlay');
  const video = document.getElementById('inAppVideo');
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
  const skipCard = document.getElementById('inAppSkipIntroCard');
  if (skipCard) skipCard.style.display = 'none';
  const nextCard = document.getElementById('inAppNextEpCard');
  if (nextCard) nextCard.style.display = 'none';
  if (video) {
    if (inAppCurrentMedia) {
      const pct = (video.duration > 0) ? Math.round((video.currentTime / video.duration) * 100) : 0;
      scrobbleTrakt('pause', inAppCurrentMedia, pct);
    }
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
    }
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  if (overlay) {
    overlay.classList.remove('active', 'idle');
  }
  hideInAppToast();
  document.body.style.overflow = '';
  clearTimeout(inAppIdleTimeout);
  refreshUserLists();
}

function toggleInAppPlay() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  if (video.paused) {
    video.play().then(() => updatePlayPauseBtn(true)).catch(() => {});
  } else {
    video.pause();
    updatePlayPauseBtn(false);
  }
}

function inAppSeekRel(delta) {
  const video = document.getElementById('inAppVideo');
  if (!video || !video.duration) return;
  video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + delta));
  resetInAppIdleTimer();
}

function seekInApp(event) {
  const video = document.getElementById('inAppVideo');
  const seekbar = document.getElementById('inAppSeekbar');
  if (!video || !video.duration || !seekbar) return;
  const rect = seekbar.getBoundingClientRect();
  const clickX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const pct = clickX / rect.width;
  video.currentTime = pct * video.duration;
  resetInAppIdleTimer();
}

function inAppPrevTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex > 0) {
    inAppPlaylistIndex--;
    const item = inAppPlaylist[inAppPlaylistIndex];
    openInAppPlayer(item.url, item.title, inAppPlaylist, inAppCurrentMedia);
  }
}

function inAppNextTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex < inAppPlaylist.length - 1) {
    inAppPlaylistIndex++;
    const item = inAppPlaylist[inAppPlaylistIndex];
    openInAppPlayer(item.url, item.title, inAppPlaylist, inAppCurrentMedia);
  }
}

function toggleInAppFullscreen() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_fullscreen && /Mac/i.test(navigator.platform || navigator.userAgent)) {
    window.pywebview.api.toggle_fullscreen();
    return;
  }
  const overlay = document.getElementById('inAppPlayerOverlay');
  if (!overlay) return;
  const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
  if (!fsEl) {
    if (overlay.requestFullscreen) {
      overlay.requestFullscreen().catch(() => {});
    } else if (overlay.webkitRequestFullscreen) {
      overlay.webkitRequestFullscreen();
    }
  } else {
    if (document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    }
  }
}

async function switchToExternalPlayer() {
  const url = inAppCurrentUrl;
  const title = inAppCurrentTitle;
  const media = inAppCurrentMedia;
  const playlist = inAppPlaylist;
  closeInAppPlayer();

  try {
    await api('/api/mpv', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        filename: title,
        pack_files: playlist && playlist.length > 1 ? playlist.map(p => ({ filename: p.title, download: p.url })) : null,
        media: media ? {
          id: media.id,
          name: media.name || title,
          type: media.type || 'movie',
          year: media.year || '',
          poster: media.poster || '',
          season: media.season || null,
          episode: media.episode || null,
        } : null
      })
    });
  } catch (e) {
    alert("Impossible d'ouvrir le lecteur KINO : " + e.message);
  }
}

const inAppVideoEl = document.getElementById('inAppVideo');
const inAppOverlayEl = document.getElementById('inAppPlayerOverlay');
const inAppPlayPauseBtnEl = document.getElementById('inAppPlayPauseBtn');
const inAppSeekFillEl = document.getElementById('inAppSeekFill');
const inAppTimeTextEl = document.getElementById('inAppTimeText');

let inAppLastSaveTime = 0;

if (inAppVideoEl) {
  inAppVideoEl.addEventListener('play', () => {
    updatePlayPauseBtn(true);
    if (!traktScrobbledStarted && inAppCurrentMedia) {
      traktScrobbledStarted = true;
      const pct = (inAppVideoEl.duration > 0) ? Math.round((inAppVideoEl.currentTime / inAppVideoEl.duration) * 100) : 0;
      scrobbleTrakt('start', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('play', inAppVideoEl.currentTime);
  });
  inAppVideoEl.addEventListener('pause', () => {
    updatePlayPauseBtn(false);
    if (inAppOverlayEl) inAppOverlayEl.classList.remove('idle');
    if (inAppCurrentMedia) {
      const pct = (inAppVideoEl.duration > 0) ? Math.round((inAppVideoEl.currentTime / inAppVideoEl.duration) * 100) : 0;
      scrobbleTrakt('pause', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('pause', inAppVideoEl.currentTime);
  });
  inAppVideoEl.addEventListener('seeked', () => {
    broadcastWpEvent('seek', inAppVideoEl.currentTime);
  });
  inAppVideoEl.addEventListener('timeupdate', () => {
    if (inAppActiveCues.length > 0) {
      updateInAppSubtitleOverlay(inAppVideoEl.currentTime);
    }
    if (inAppVideoEl.duration) {
      const curT = inAppVideoEl.currentTime;
      const durT = inAppVideoEl.duration;
      const pct = (curT / durT) * 100;
      if (inAppSeekFillEl) inAppSeekFillEl.style.width = pct + '%';
      if (inAppTimeTextEl) inAppTimeTextEl.textContent = `${formatTime(curT)} / ${formatTime(durT)}`;

      // Scrobble Trakt.tv automatique dès 80% de visionnage
      if (pct >= 80 && !traktScrobbledStop && inAppCurrentMedia) {
        traktScrobbledStop = true;
        scrobbleTrakt('stop', inAppCurrentMedia, Math.round(pct));
        showInAppToast('✓ Scrobblé sur Trakt.tv (80%)', 2200);
      }

      // Bouton Skip Intro (+85s) pour les séries entre 15s et 150s
      const isSeriesNow = Boolean((inAppCurrentMedia && inAppCurrentMedia.type === 'series') || inAppPlaylist.length > 1);
      const skipCard = document.getElementById('inAppSkipIntroCard');
      if (skipCard) {
        if (isSeriesNow && !inAppSkipIntroDismissed && curT >= 15 && curT <= 150 && durT > 600) {
          skipCard.style.display = 'flex';
        } else {
          skipCard.style.display = 'none';
        }
      }

      // Carte Prochain épisode dans les 22 dernières secondes
      const nextCard = document.getElementById('inAppNextEpCard');
      const hasNextTrack = inAppPlaylist.length > 1 && inAppPlaylistIndex < inAppPlaylist.length - 1;
      if (nextCard) {
        const remSec = durT - curT;
        if (hasNextTrack && !inAppNextEpCancelled && durT > 300 && remSec > 0.5 && remSec <= 22) {
          nextCard.style.display = 'flex';
          const nextItem = inAppPlaylist[inAppPlaylistIndex + 1];
          const nextTitleEl = document.getElementById('inAppNextEpTitle');
          const nextCountEl = document.getElementById('inAppNextEpCountdown');
          if (nextTitleEl && nextItem) nextTitleEl.textContent = nextItem.title || 'Épisode suivant';
          if (nextCountEl) nextCountEl.textContent = String(Math.max(1, Math.ceil(remSec)));
        } else {
          nextCard.style.display = 'none';
        }
      }

      const now = Date.now();
      if (inAppCurrentMedia && inAppCurrentMedia.id && now - inAppLastSaveTime > 10000 && curT > 5) {
        inAppLastSaveTime = now;
        const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
        const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
        api('/api/history-record', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            media: {
              id: inAppCurrentMedia.id,
              name: inAppCurrentMedia.name || inAppCurrentTitle,
              type: inAppCurrentMedia.type || 'movie',
              year: inAppCurrentMedia.year || '',
              poster: inAppCurrentMedia.poster || '',
              season: s,
              episode: e,
              filename: inAppCurrentTitle,
              position: Math.round(curT),
              duration: Math.round(durT)
            }
          })
        }).catch(() => {});
      }
    }
  });
  inAppVideoEl.addEventListener('ended', () => {
    if (inAppCurrentMedia) {
      scrobbleTrakt('stop', inAppCurrentMedia, 100);
    }
    if (inAppPlaylist.length > 1 && inAppPlaylistIndex < inAppPlaylist.length - 1) {
      inAppNextTrack();
    }
  });
  inAppVideoEl.addEventListener('click', () => {
    if (inAppVideoEl.muted && !inAppVideoEl._userMuted) {
      inAppVideoEl.muted = false;
      updateInAppVolUI();
    }
    toggleInAppPlay();
  });
}

if (inAppOverlayEl) {
  inAppOverlayEl.addEventListener('mousemove', resetInAppIdleTimer);
  inAppOverlayEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    inAppWheelVolume(e.deltaY);
  }, { passive: false });
}

window.addEventListener('keydown', (e) => {
  const overlay = document.getElementById('inAppPlayerOverlay');
  const isPlayerActive = overlay && overlay.classList.contains('active');
  const isInput = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement ? document.activeElement.tagName : '');

  if (!isPlayerActive) {
    // Global macOS & keyboard shortcuts
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      const si = document.getElementById('searchInput');
      if (si) { si.focus(); si.select(); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === ',') {
      e.preventDefault();
      openConfig();
      return;
    }
    if (e.key === '/' && !isInput) {
      e.preventDefault();
      const si = document.getElementById('searchInput');
      if (si) { si.focus(); si.select(); }
      return;
    }
    const csm = document.getElementById('caseModal');
    const isCaseOpen = Boolean(csm && (csm.classList.contains('active') || csm.style.display === 'flex'));
    if (isCaseOpen && !isInput) {
      if (e.key === ' ') {
        e.preventDefault();
        if (caseSpinState && !caseSpinState.finished) {
          skipCaseSpin();
        } else {
          surpriseMeMedia(activeCasePoolMode || 'catalog');
        }
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        surpriseMeMedia(activeCasePoolMode || 'catalog');
        return;
      }
    }
    if (e.key === '?' && !isInput) {
      e.preventDefault();
      toggleShortcutsModal();
      return;
    }
    if (e.key === 'Escape') {
      const sm = document.getElementById('shortcutsModal');
      if (sm && sm.style.display === 'flex') {
        toggleShortcutsModal();
        return;
      }
      const lbm = document.getElementById('letterboxdModal');
      if (lbm && (lbm.classList.contains('active') || lbm.style.display === 'flex')) {
        closeLetterboxdModal();
        return;
      }
      if (isCaseOpen) {
        closeCaseModal();
        return;
      }
      const tm = document.getElementById('trailerModal');
      if (tm && (tm.classList.contains('active') || tm.style.display === 'flex')) {
        closeTrailerModal();
        return;
      }
      const cm = document.getElementById('configModal');
      if (cm && (cm.classList.contains('active') || cm.style.display === 'flex')) {
        closeConfig();
        return;
      }
      if (isInput && document.activeElement) {
        document.activeElement.blur();
        return;
      }
      const dp = document.getElementById('detailPanel');
      if (dp && dp.style.display === 'block') {
        dp.style.display = 'none';
        document.getElementById('torrentsPanel').style.display = 'none';
        return;
      }
    }
    return;
  }

  if (isInput) return;

  resetInAppIdleTimer();

  if (e.key === ' ' || e.key === 'k' || e.key === 'K') {
    e.preventDefault();
    toggleInAppPlay();
  } else if (e.key === 'ArrowLeft' || e.key === 'j' || e.key === 'J') {
    e.preventDefault();
    inAppSeekRel(-10);
  } else if (e.key === 'ArrowRight' || e.key === 'l' || e.key === 'L') {
    e.preventDefault();
    inAppSeekRel(10);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    const v = document.getElementById('inAppVideo');
    if (v) setInAppVolume(v.volume + 0.05, true);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    const v = document.getElementById('inAppVideo');
    if (v) setInAppVolume(v.volume - 0.05, true);
  } else if (e.key === 'f' || e.key === 'F') {
    e.preventDefault();
    toggleInAppFullscreen();
  } else if (e.key === 'm' || e.key === 'M') {
    e.preventDefault();
    toggleInAppMute();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    const sm = document.getElementById('shortcutsModal');
    if (sm && sm.style.display === 'flex') {
      toggleShortcutsModal();
      return;
    }
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      closeInAppPlayer();
    }
  } else if (e.key === '>' || e.key === 'n' || e.key === 'N') {
    e.preventDefault();
    inAppNextTrack();
  } else if (e.key === '<' || e.key === 'p' || e.key === 'P') {
    e.preventDefault();
    inAppPrevTrack();
  } else if (e.key === 'b' || e.key === 'B') {
    e.preventDefault();
    cycleInAppClarity();
  } else if (e.key === 'c' || e.key === 'C') {
    e.preventDefault();
    cycleInAppSubtitles();
  } else if (e.key === 'v' || e.key === 'V') {
    e.preventDefault();
    cycleInAppAudioBoost();
  } else if (e.key === 'i' || e.key === 'I') {
    e.preventDefault();
    toggleInAppPiP();
  } else if (e.key === 's' || e.key === 'S') {
    e.preventDefault();
    skipInAppIntro();
  } else if (e.key === ']') {
    e.preventDefault();
    cycleInAppSpeed(1);
  } else if (e.key === '[') {
    e.preventDefault();
    cycleInAppSpeed(-1);
  } else if (e.key === 'z' || e.key === 'Z') {
    e.preventDefault();
    adjustInAppSubDelay(-0.25);
  } else if (e.key === 'x' || e.key === 'X') {
    e.preventDefault();
    adjustInAppSubDelay(0.25);
  } else if (e.key === 'e' || e.key === 'E') {
    e.preventDefault();
    switchToExternalPlayer();
  } else if (e.key === '?') {
    e.preventDefault();
    toggleShortcutsModal();
  } else if (e.key === 'g' || e.key === 'G') {
    e.preventDefault();
    adjustInAppSubDelay(-0.5);
  } else if (e.key === 'h' || e.key === 'H') {
    e.preventDefault();
    adjustInAppSubDelay(0.5);
  }
});

async function pollDownloads() {
  const data = await api('/api/downloads');
  const items = Object.values(data.downloads || {});
  const panel = document.getElementById('downloadsPanel');
  if (!items.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  document.getElementById('downloadsList').innerHTML = items.map(d => `
    <div style="margin-bottom:8px; background:var(--bg); padding:12px; border-radius:6px; border:1px solid var(--border);">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; font-size:0.86rem;">
        <strong>${d.filename}</strong>
        <div style="display:flex; align-items:center; gap:10px; color:var(--muted); font-size:0.8rem;">
          <span>${
            d.status === 'completed' ? 'Terminé' :
            d.status === 'cancelled' ? 'Annulé' :
            d.status.startsWith('error') ? d.status :
            `${d.progress}% (${d.speed})`
          }</span>
          ${d.status === 'downloading' ? `<button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick='cancelDownload(${JSON.stringify(d.id)})'>Annuler</button>` : ''}
        </div>
      </div>
      <div style="font-size:0.76rem; color:var(--dim); margin-top:4px;">${d.downloaded} / ${d.total} — ${d.path}</div>
      <div class="progress-bar"><div class="progress-fill" style="width:${d.progress}%"></div></div>
    </div>
  `).join('');

  if (items.some(d => d.status === 'downloading')) {
    setTimeout(pollDownloads, 1000);
  }
}

checkConfig();
checkTraktStatus();
startRemotePolling();
pollDownloads();
switchTab('movies');
initResizeHandles();
syncWindowState();
</script>
</body>
</html>
"""


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload, status=200):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def handle_auto_stream(self, params):
        dl_url = resolve_auto_stream_episode(params)
        self.send_response(302)
        self.send_header("Location", dl_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == "/api/auto-stream":
            try:
                self.handle_auto_stream(params)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            if parsed.path == "/":
                raw = HTML_PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if parsed.path == "/remote":
                remote_html = remote_controller.generate_remote_html(PORT).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(remote_html)))
                self.end_headers()
                self.wfile.write(remote_html)
                return

            if parsed.path == "/api/remote/state":
                self.send_json(remote_controller.get_player_state())
                return

            if parsed.path == "/qrcode.min.js":
                if os.path.exists("qrcode.min.js"):
                    with open("qrcode.min.js", "rb") as qrf:
                        raw = qrf.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return

            if parsed.path in ("/api/remote/info", "/api/remote/qr"):
                local_ip = remote_controller.get_local_ip()
                remote_url = f"http://{local_ip}:{PORT}/remote"
                self.send_json({"status": "ok", "url": remote_url, "local_ip": local_ip, "port": PORT})
                return

            if parsed.path == "/api/player/commands":
                cmds = remote_controller.pop_commands()
                self.send_json({"commands": cmds})
                return

            if parsed.path == "/api/addons":
                addons = addon_manager.list_addons()
                self.send_json({"status": "ok", "addons": addons})
                return

            if parsed.path == "/api/anime/trending":
                items = anime_engine.get_trending_anime()
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/popular":
                limit = int(params.get("limit", 20))
                items = anime_engine.get_popular_anime(limit=limit)
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/search":
                q = params.get("q", "").strip()
                items = anime_engine.search_anime(q) if q else []
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/filler":
                title = params.get("title", "")
                ep = int(params.get("episode", 1))
                res = anime_engine.is_episode_filler(title, ep)
                self.send_json({"status": "ok", "checked": True, **res, "filler": res})
                return

            if parsed.path == "/api/community/curated":
                curated = community_lists.get_curated_lists()
                self.send_json({"status": "ok", "lists": curated, "collections": curated})
                return

            if parsed.path == "/api/trakt/status":
                connected = trakt_engine.is_trakt_connected()
                user_info = kino_db.get_config("trakt_user")
                uname = user_info.get("username") if isinstance(user_info, dict) else None
                self.send_json({"status": "ok", "authenticated": connected, "connected": connected, "username": uname, "user": user_info})
                return

            if parsed.path == "/api/remux":
                raw_url = params.get("url", "")
                ss = params.get("ss", "0")
                if not raw_url:
                    self.send_json({"error": "Paramètre url manquant"}, status=400)
                    return
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    self.send_json({"error": "FFmpeg introuvable sur le système"}, status=500)
                    return
                try:
                    start_sec = max(0.0, float(ss))
                except (ValueError, TypeError):
                    start_sec = 0.0

                cmd = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel", "error",
                ]
                if start_sec > 0:
                    cmd.extend(["-ss", f"{start_sec:.2f}"])
                cmd.extend([
                    "-i", raw_url,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ac", "2",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-f", "mp4",
                    "pipe:1"
                ])

                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "none")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()

                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    while True:
                        chunk = proc.stdout.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                return

            if parsed.path == "/api/auto-stream":
                self.handle_auto_stream(params)
                return

            if parsed.path == "/api/ad-dl":
                prov, tok = _get_provider_and_token(provider="alldebrid")
                raw_link = params.get("link", "")
                q = urllib.parse.urlencode({"agent": "KINO", "apikey": tok, "link": raw_link})
                u = http_json(f"https://api.alldebrid.com/v4/link/unlock?{q}")
                dl_url = ((u.get("data") or {}).get("link")) or ""
                if not dl_url:
                    raise RuntimeError("Impossible de débrider ce lien AllDebrid.")
                self.send_response(302)
                self.send_header("Location", dl_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            if parsed.path == "/api/torbox-dl":
                prov, tok = _get_provider_and_token(provider="torbox")
                tid = params.get("torrent_id", "")
                fid = params.get("file_id", "0")
                auth = {"Authorization": f"Bearer {tok}"}
                res = http_json(f"https://api.torbox.app/v1/api/torrents/requestdl?token={tok}&torrent_id={tid}&file_id={fid}", headers=auth)
                dl_url = res.get("data") or ""
                if not dl_url:
                    raise RuntimeError("Impossible d'obtenir le lien TorBox.")
                self.send_response(302)
                self.send_header("Location", dl_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            if parsed.path == "/api/config":
                cfg = load_config()
                prov = cfg.get("debrid_provider", "realdebrid")
                tok = cfg.get("rd_token", "")
                prov_tokens = cfg.get("provider_tokens") or {}
                user_info = None
                ret_days = int(cfg.get("rd_retention_days", 0) or 0)
                if tok:
                    try:
                        user_info = rd_get_user(tok, provider=prov)
                    except Exception:
                        user_info = None
                    if ret_days > 0:
                        threading.Thread(target=rd_cleanup_cloud, args=(tok, ret_days, prov), daemon=True).start()
                self.send_json({
                    "debrid_provider": prov,
                    "configured_providers": {k: bool(v) for k, v in prov_tokens.items()},
                    "has_token": bool(tok),
                    "user": user_info,
                    "download_dir": cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)),
                    "player_mode": cfg.get("player_mode", "kino"),
                    "pref_lang": cfg.get("pref_lang", "vf"),
                    "pref_quality": cfg.get("pref_quality", "4k"),
                    "hdr_mode": cfg.get("hdr_mode", "sdr_pref"),
                    "audio_mode": cfg.get("audio_mode", "voice_boost"),
                    "rd_retention_days": ret_days,
                })
                return

            if parsed.path == "/api/subtitles":
                imdb_id = params.get("imdb_id", "")
                mtype = params.get("type", "movie")
                season = params.get("season", 1)
                episode = params.get("episode", 1)
                subs = fetch_opensubtitles(imdb_id, mtype, season, episode) if imdb_id else []
                self.send_json({"subtitles": subs})
                return

            if parsed.path == "/api/subtitle-cues":
                sub_url = (params.get("url") or "").strip()
                if not sub_url or not sub_url.startswith("http"):
                    self.send_json({"cues": []})
                    return
                req = urllib.request.Request(sub_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    raw_bytes = resp.read()
                try:
                    srt_text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    srt_text = raw_bytes.decode("latin-1", errors="ignore")
                cues = parse_srt_cues(srt_text)
                self.send_json({"cues": cues})
                return

            if parsed.path == "/api/catalog":
                mtype = params.get("type", "movie")
                genre = params.get("genre", "")
                sort = params.get("sort", "top")
                skip = int(params.get("skip", "0") or 0)
                metas = get_catalog_top(mtype, genre, skip=skip, sort=sort)
                self.send_json({"metas": metas})
                return

            if parsed.path == "/api/trailer":
                title = params.get("title", "")
                year = params.get("year", "")
                yt_id = params.get("yt_id", "")
                lang = params.get("lang", "vf")
                info = resolve_trailer_info(title=title, year=year, yt_id=yt_id, lang=lang)
                self.send_json(info)
                return

            if parsed.path == "/api/search-suggest":
                q = params.get("q", "").strip()
                mtype = params.get("type", "")
                results = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=8)
                self.send_json({"ok": True, "results": results})
                return

            if parsed.path == "/api/search":
                q = params.get("q", "")
                mtype = params.get("type", "movie")
                metas = search_cinemeta(q, mtype)
                if not metas:
                    local_matches = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=20)
                    if local_matches:
                        metas = local_matches
                self.send_json({"metas": metas})
                return

            if parsed.path == "/api/meta":
                imdb_id = params.get("imdb_id", "")
                mtype = params.get("type", "movie")
                meta = get_media_meta(imdb_id, mtype)
                self.send_json({"meta": meta})
                return

            if parsed.path == "/api/series-meta":
                imdb_id = params.get("imdb_id", "")
                meta = get_series_meta(imdb_id)
                self.send_json({"meta": meta})
                return

            if parsed.path == "/api/user-lists":
                cfg = load_config()
                self.send_json({
                    "watchlist": cfg.get("watchlist", []),
                    "history": cfg.get("history", []),
                    "letterboxd_user": cfg.get("letterboxd_user", ""),
                })
                return

            if parsed.path == "/api/sync/gdrive":
                res = kino_db.db_sync_gdrive()
                self.send_json(res)
                return

            if parsed.path == "/api/rd-history":
                cfg = load_config()
                prov = cfg.get("debrid_provider", "realdebrid")
                tok = cfg.get("rd_token", "")
                ret_days = int(cfg.get("rd_retention_days", 0) or 0)
                cleaned = {"deleted_downloads": 0, "deleted_torrents": 0}
                if tok and ret_days > 0:
                    cleaned = rd_cleanup_cloud(tok, ret_days, provider=prov)
                items = rd_get_downloads(tok, limit=50, provider=prov)
                self.send_json({"items": items, "cleaned": cleaned})
                return

            if parsed.path == "/api/torrents":
                imdb_id = params.get("imdb_id")
                mtype = params.get("type", "movie")
                season = params.get("season", 1)
                episode = params.get("episode", 1)
                q = params.get("q") or params.get("title", "")
                runtime = params.get("runtime", "")
                sort_by = params.get("sort_by", "score")

                runtime_min = 0
                if runtime:
                    m_rt = re.search(r"(\d+)", str(runtime))
                    if m_rt:
                        runtime_min = int(m_rt.group(1))

                torrents = []
                clean_q = q.split(" — ")[0].strip() if " — " in q else q.strip()
                if imdb_id:
                    try:
                        torrents.extend(search_torrentio(imdb_id, mtype, season, episode, runtime_minutes=runtime_min, sort_by=sort_by, query_title=clean_q))
                    except Exception:
                        pass
                if not torrents and (q or clean_q):
                    torrents.extend(search_apibay(clean_q or q))

                torrents = [t for t in torrents if is_plausible_torrent_size(t, media_type=mtype, runtime_minutes=runtime_min)]
                self.send_json({"torrents": torrents})
                return

            if parsed.path == "/api/letterboxd/custom-lists":
                cfg = load_config()
                self.send_json({"custom_lists": cfg.get("custom_lists", [])})
                return

            if parsed.path == "/api/downloads":
                with DOWNLOADS_LOCK:
                    self.send_json({"downloads": DOWNLOADS})
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self.read_json_body()

            if parsed.path == "/api/remote/control":
                action = body.get("action", "")
                val = body.get("value")
                remote_controller.push_command(action, val)
                self.send_json({"ok": True, "action": action})
                return

            if parsed.path == "/api/player/state":
                remote_controller.update_player_state(body)
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/addons/toggle":
                aid = body.get("id", "")
                en = bool(body.get("enabled", True))
                addon_manager.toggle_addon(aid, en)
                self.send_json({"ok": True, "addons": addon_manager.list_addons()})
                return

            if parsed.path == "/api/trakt/auth/start":
                res = trakt_engine.start_device_auth()
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/auth/poll":
                dcode = body.get("device_code", "")
                res = trakt_engine.poll_device_token(dcode)
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/disconnect":
                res = trakt_engine.disconnect_trakt()
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/scrobble":
                act = body.get("action", "start")
                meta = body.get("media", {})
                prog = float(body.get("progress", 0.0))
                res = trakt_engine.scrobble_action(act, meta, prog)
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/sync":
                res = trakt_engine.sync_trakt_watchlist()
                self.send_json(res)
                return

            if parsed.path == "/api/config":
                updates = {}
                if body.get("debrid_provider"):
                    updates["debrid_provider"] = body["debrid_provider"].strip().lower()
                if body.get("rd_token"):
                    updates["rd_token"] = body["rd_token"].strip()
                if body.get("download_dir"):
                    updates["download_dir"] = body["download_dir"].strip()
                if body.get("player_mode"):
                    updates["player_mode"] = body["player_mode"].strip()
                if body.get("pref_lang"):
                    updates["pref_lang"] = body["pref_lang"].strip()
                if body.get("pref_quality"):
                    updates["pref_quality"] = body["pref_quality"].strip()
                if body.get("hdr_mode"):
                    updates["hdr_mode"] = body["hdr_mode"].strip()
                if body.get("audio_mode"):
                    updates["audio_mode"] = body["audio_mode"].strip()
                if "rd_retention_days" in body and body["rd_retention_days"] is not None:
                    updates["rd_retention_days"] = int(body["rd_retention_days"])
                save_config(updates)
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/watched-toggle":
                hist = toggle_watched_status(body)
                self.send_json({"ok": True, "history": hist})
                return

            if parsed.path == "/api/upload-torrent":
                b64_data = body.get("data_b64") or ""
                if not b64_data:
                    raise RuntimeError("Fichier .torrent vide.")
                raw_bytes = base64.b64decode(b64_data)
                info = torrent_file_to_magnet(raw_bytes)
                self.send_json({"ok": True, **info})
                return

            if parsed.path == "/api/save-screenshot":
                cfg = load_config()
                folder = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
                folder.mkdir(parents=True, exist_ok=True)
                data_url = body.get("data_url") or ""
                if "," in data_url:
                    data_url = data_url.split(",", 1)[1]
                raw_png = base64.b64decode(data_url)
                safe_title = re.sub(r"[^a-zA-Z0-9_\-]+", "_", (body.get("title") or "KINO").strip())[:45].strip("_") or "KINO"
                t_str = re.sub(r"[^a-zA-Z0-9_\-]+", "", (body.get("time_str") or "00m00s"))
                fname = f"KINO_Capture_{safe_title}_{t_str}.png"
                out_path = folder / fname
                out_path.write_bytes(raw_png)
                self.send_json({"ok": True, "filename": fname, "path": str(out_path)})
                return

            if parsed.path == "/api/rd-delete":
                cfg = load_config()
                deleted = rd_delete_downloads(
                    cfg.get("rd_token", ""),
                    body.get("ids") or body.get("id"),
                    provider=cfg.get("debrid_provider", "realdebrid"),
                )
                self.send_json({"ok": True, "deleted": deleted})
                return

            if parsed.path == "/api/rd-cleanup":
                cfg = load_config()
                max_age = body.get("max_age_days")
                cleaned = rd_cleanup_cloud(
                    cfg.get("rd_token", ""),
                    max_age_days=max_age,
                    provider=cfg.get("debrid_provider", "realdebrid"),
                )
                self.send_json({"ok": True, "cleaned": cleaned})
                return

            if parsed.path == "/api/watchlist":
                wl = toggle_watchlist(body)
                self.send_json({"ok": True, "watchlist": wl})
                return

            if parsed.path == "/api/import-letterboxd":
                res = import_letterboxd_watchlist(body)
                self.send_json({"ok": True, **res})
                return

            if parsed.path == "/api/letterboxd/import-list":
                url = body.get("url", "")
                res = import_letterboxd_custom_list(url)
                self.send_json({"ok": True, **res})
                return

            if parsed.path == "/api/letterboxd/delete-list":
                lid = body.get("id", "")
                lists = delete_letterboxd_custom_list(lid)
                self.send_json({"ok": True, "custom_lists": lists})
                return

            if parsed.path == "/api/history-remove":
                hist = remove_history(body.get("id", ""))
                self.send_json({"ok": True, "history": hist})
                return

            if parsed.path == "/api/history-record":
                m = body.get("media")
                if isinstance(m, dict) and m.get("id"):
                    hist = record_history(m)
                    self.send_json({"ok": True, "history": hist})
                    return
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/sync/gdrive":
                res = kino_db.db_sync_gdrive()
                self.send_json(res)
                return

            if parsed.path == "/api/window/action":
                act = body.get("action")
                if WINDOW_ACTION_CALLBACK:
                    WINDOW_ACTION_CALLBACK(act)
                    self.send_json({"ok": True})
                    return
                self.send_json({"ok": False, "note": "Pas de callback fenetre"})
                return

            if parsed.path == "/api/open-folder":
                cfg = load_config()
                folder = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
                folder.mkdir(parents=True, exist_ok=True)
                if sys.platform == "win32":
                    spawn_on_user_desktop(["explorer.exe", str(folder)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/debrid":
                cfg = load_config()
                res = rd_debrid_magnet(
                    cfg.get("rd_token", ""),
                    body.get("magnet", ""),
                    season=body.get("season"),
                    episode=body.get("episode"),
                    resolve_url=body.get("resolve_url"),
                )
                self.send_json(res)
                return

            if parsed.path == "/api/one-click-play":
                cfg = load_config()
                token = cfg.get("rd_token", "")
                if not token:
                    raise RuntimeError("Token API Real-Debrid manquant.")
                imdb_id = body.get("imdb_id", "")
                mtype = body.get("type", "movie")
                season = body.get("season", 1)
                episode = body.get("episode", 1)
                title = body.get("title", "KINO")
                series_name = body.get("name") or title.split(" — ")[0]
                player_mode = body.get("player_mode") or cfg.get("player_mode", "kino")

                runtime_min = 0
                if imdb_id and mtype == "movie":
                    try:
                        m_info = get_media_meta(imdb_id, "movie")
                        m_rt = re.search(r"(\d+)", str((m_info or {}).get("runtime") or ""))
                        if m_rt:
                            runtime_min = int(m_rt.group(1))
                    except Exception:
                        pass

                is_series = mtype in ("series", "anime", "tv")
                torrents = search_torrentio(imdb_id, mtype, season, episode, runtime_minutes=runtime_min, query_title=series_name) if imdb_id else []
                if not torrents and (title or series_name):
                    torrents = search_apibay(series_name or title)
                torrents = [t for t in torrents if is_plausible_torrent_size(t, media_type=mtype, runtime_minutes=runtime_min)]
                if not torrents:
                    raise RuntimeError("Aucun flux valide trouvé pour ce titre.")

                torrents.sort(key=score_torrent_for_one_click, reverse=True)

                last_err = None
                for cand in torrents[:10]:
                    try:
                        res = rd_debrid_magnet(
                            token,
                            cand.get("magnet", ""),
                            season=season if is_series else None,
                            episode=episode if is_series else None,
                            resolve_url=cand.get("resolve_url", ""),
                        )
                        if res.get("ready") and res.get("files"):
                            target_files = [f for f in res["files"] if f.get("is_target_ep")]
                            if is_series and len(res["files"]) > 1 and not target_files:
                                # Aucun fichier ne correspond à l'épisode recherché dans ce pack -> essayer candidat suivant !
                                continue
                            target_file = target_files[0] if target_files else res["files"][0]
                            playlist_items = None
                            if is_series and imdb_id:
                                playlist_items = build_series_playlist_items(
                                    imdb_id=imdb_id,
                                    season=season,
                                    start_ep=episode,
                                    first_url=target_file["download"],
                                    series_name=series_name,
                                    year=body.get("year", ""),
                                    poster=body.get("poster", ""),
                                    pref_hash=cand.get("info_hash", ""),
                                    first_filename=target_file.get("filename", ""),
                                )
                                prefetch_next_episode(
                                    imdb_id=imdb_id,
                                    season=season,
                                    next_ep=int(episode) + 1,
                                    pref_hash=cand.get("info_hash", ""),
                                )
                            resume_sec = get_resume_position(
                                imdb_id,
                                season if is_series else None,
                                episode if is_series else None,
                            ) if imdb_id else 0
                            media_ctx = {
                                "id": imdb_id,
                                "name": series_name,
                                "type": mtype,
                                "year": body.get("year", ""),
                                "poster": body.get("poster", ""),
                                "season": int(season) if is_series else None,
                                "episode": int(episode) if is_series else None,
                                "filename": target_file["filename"],
                            } if imdb_id else None
                            mpv_info = None
                            if player_mode in ("external", "kino"):
                                mpv_info = launch_mpv(
                                    target_file["download"],
                                    title,
                                    playlist_items=playlist_items,
                                    media_ctx=media_ctx,
                                    start_sec=resume_sec,
                                )
                                mpv_info["mode"] = "kino"
                            else:
                                mpv_info = {
                                    "mode": "integrated",
                                    "playlist_count": len(playlist_items) if playlist_items else 1
                                }
                            if media_ctx:
                                record_history(media_ctx)
                            self.send_json({
                                "ok": True,
                                "chosen_torrent": f"{cand.get('source', '')} {cand.get('title', '')}".strip(),
                                "debrid": res,
                                "file": target_file,
                                "stream_url": target_file["download"],
                                "playlist": playlist_items or [],
                                "mpv": mpv_info,
                                "resume_sec": resume_sec,
                            })
                            return
                    except Exception as e:
                        last_err = e
                        continue

                raise RuntimeError(f"Impossible de lancer un flux instantané ({last_err or 'aucun cache RD+ prêt'}).")

            if parsed.path == "/api/debrid-status":
                cfg = load_config()
                res = rd_check_torrent(
                    cfg.get("rd_token", ""),
                    body.get("torrent_id", ""),
                    season=body.get("season"),
                    episode=body.get("episode"),
                )
                self.send_json(res)
                return

            if parsed.path == "/api/download":
                cfg = load_config()
                dl_id = start_background_download(
                    body["url"],
                    body["filename"],
                    cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)),
                )
                self.send_json({"dl_id": dl_id})
                return

            if parsed.path == "/api/download-cancel":
                dl_id = body.get("dl_id")
                with DOWNLOADS_LOCK:
                    if dl_id in DOWNLOADS:
                        DOWNLOADS[dl_id]["cancel"] = True
                self.send_json({"ok": True})
                return

            if parsed.path in ("/api/mpv", "/api/vlc"):
                url = body.get("url", "")
                filename = body.get("filename", "")
                media = body.get("media")
                pack_files = body.get("pack_files")
                playlist_items = None
                if isinstance(pack_files, list) and len(pack_files) > 1:
                    playlist_items = [
                        {"title": f.get("filename", "Épisode"), "url": f.get("download", "")}
                        for f in pack_files
                        if f.get("download")
                    ]
                elif isinstance(media, dict) and media.get("type") == "series" and media.get("id"):
                    playlist_items = build_series_playlist_items(
                        imdb_id=media["id"],
                        season=media.get("season") or 1,
                        start_ep=media.get("episode") or 1,
                        first_url=url,
                        series_name=media.get("name", ""),
                        year=media.get("year", ""),
                        poster=media.get("poster", ""),
                        first_filename=filename,
                    )
                    prefetch_next_episode(
                        imdb_id=media["id"],
                        season=media.get("season") or 1,
                        next_ep=int(media.get("episode") or 1) + 1,
                    )
                display_title = filename
                media_ctx = None
                resume_sec = 0
                if isinstance(media, dict) and media.get("name"):
                    display_title = media["name"]
                    if media.get("season") and media.get("episode"):
                        display_title = f"{media['name']} — S{int(media['season']):02d}E{int(media['episode']):02d}"
                if isinstance(media, dict) and media.get("id"):
                    media_ctx = {
                        "id": media["id"],
                        "name": media.get("name", filename),
                        "type": media.get("type", "movie"),
                        "year": media.get("year", ""),
                        "poster": media.get("poster", ""),
                        "season": int(media["season"]) if media.get("season") else None,
                        "episode": int(media["episode"]) if media.get("episode") else None,
                        "filename": filename,
                    }
                    resume_sec = get_resume_position(
                        media["id"],
                        media_ctx["season"],
                        media_ctx["episode"],
                    )
                mpv_bin = launch_mpv(
                    url,
                    display_title,
                    playlist_items=playlist_items,
                    media_ctx=media_ctx,
                    start_sec=resume_sec,
                )
                if media_ctx:
                    record_history(media_ctx)
                self.send_json({"ok": True, "mpv": mpv_bin, "resume_sec": resume_sec})
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)


if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    except Exception:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", PORT), RequestHandler)
        except OSError:
            if "--no-browser" not in sys.argv:
                webbrowser.open(url)
            sys.exit(0)

    print(f"\nKINO est lancé sur : {url}")
    print("Appuyez sur Ctrl+C pour arrêter.\n")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")


