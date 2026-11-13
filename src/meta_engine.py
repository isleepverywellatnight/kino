#!/usr/bin/env python3
"""
KINO Metadata & Catalog Engine
Moteur de recherche de catalogues (Cinemeta, TMDB), classiques, animés japonais,
traductions françaises automatiques, gestion des sous-titres OpenSubtitles,
bandes-annonces YouTube, import Letterboxd et gestion de la Watchlist.
"""

import concurrent.futures
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import anime_engine
import kino_db
from config import HEADERS, MEM_CACHE, TEMP_DIR, cached_get, http_json, load_config, save_config

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
    "Music": "Musique",
    "Musical": "Comédie musicale",
    "Sport": "Sport",
    "Film-Noir": "Film noir",
    "Short": "Court métrage",
    "Reality-TV": "Téléréalité",
    "Talk-Show": "Talk-show",
    "Game-Show": "Jeu télévisé",
    "News": "Actualités",
}


def is_already_french(text):
    """Detecte rapidement si le texte est deja en francais pour eviter les requetes inutiles."""
    if not text:
        return True
    sample = (" " + text.lower() + " ")[:350]
    french_indicators = (
        " le ", " la ", " les ", " des ", " un ", " une ", " dans ", " pour ",
        " avec ", " et ", " qui ", " son ", " sa ", " ses ", " plus ", " mais ",
        " sur ", " par ", " est ", " une "
    )
    return sum(1 for w in french_indicators if w in sample) >= 3


def translate_text_fr(text):
    text = (text or "").strip()
    if not text or is_already_french(text):
        return text
    h = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    cache_key = f"tr_fr_v2:{h}"

    def _do():
        try:
            u = (
                "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=fr&dt=t&q="
                + urllib.parse.quote(text)
            )
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                out = "".join(seg[0] for seg in (res[0] or []) if seg and seg[0]).strip()
                if out:
                    return out
        except Exception:
            pass
        try:
            u2 = "https://api.mymemory.translated.net/get?langpair=en|fr&q=" + urllib.parse.quote(text[:480])
            req2 = urllib.request.Request(u2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=1.5) as resp2:
                d2 = json.loads(resp2.read().decode("utf-8"))
                out2 = ((d2.get("responseData") or {}).get("translatedText") or "").strip()
                if out2 and "MYMEMORY WARNING" not in out2.upper():
                    return out2
        except Exception:
            pass
        return None  # not cached: a later call tries again

    return cached_get(cache_key, 604800, _do) or text


def episode_overviews_fr(imdb_id, season):
    """French summaries of one season's episodes: {episode number: text}.
    Episodes whose translation failed are left out (the page keeps the original)."""
    try:
        season = int(season)
    except (TypeError, ValueError):
        return {}
    videos = [v for v in (get_series_meta(imdb_id) or {}).get("videos") or []
              if int(v.get("season") or 0) == season and (v.get("overview") or v.get("description"))]
    if not videos:
        return {}

    def one(video):
        original = html.unescape(str(video.get("overview") or video.get("description") or "")).strip()
        translated = translate_text_fr(original)
        if translated and (translated != original or is_already_french(original)):
            return int(video.get("episode") or video.get("number") or 0), translated
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, videos[:60]))
    return {str(number): text for number, text in filter(None, results) if number > 0}


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
    ("tt0099685", "Goodfellas (Les Affranchis)", "1990", "8.7", ["Biography", "Crime", "Drama"], "L'ascension et la chute brutale d'Henry Hill au sein de la mafia new-yorkaise sur trois décennies."),
    ("tt0073486", "One Flew Over the Cuckoo's Nest (Vol au-dessus d'un nid de coucou)", "1975", "8.7", ["Drama"], "Pour échapper à la prison, un criminel rebelle simule la folie et bouleverse le quotidien étouffant d'un asile psychiatrique dirigé par l'infirmière Ratched."),
    ("tt0047478", "Seven Samurai (Les Sept Samouraïs)", "1954", "8.6", ["Action", "Drama"], "Au Japon féodal, les habitants d'un village de paysans engagent sept samouraïs sans maître pour les défendre contre des bandits pillards."),
    ("tt0114369", "Se7en", "1995", "8.6", ["Crime", "Drama", "Mystery", "Thriller"], "Deux inspecteurs de police traquent un tueur en série méticuleux dont les meurtres atroces s'inspirent des sept péchés capitaux."),
    ("tt0102926", "The Silence of the Lambs (Le Silence des agneaux)", "1991", "8.6", ["Crime", "Drama", "Thriller"], "Une jeune recrue du FBI consulte le machiavélique docteur Hannibal Lecter, psychiatre et cannibale emprisonné, pour coincer un autre tueur."),
    ("tt0816692", "Interstellar", "2014", "8.7", ["Adventure", "Drama", "Sci-Fi"], "Une équipe d'explorateurs franchit un trou de ver dans l'espace pour tenter de trouver une planète habitable et sauver l'humanité de l'extinction."),
    ("tt0118799", "Life Is Beautiful (La vie est belle)", "1997", "8.6", ["Comedy", "Drama", "Romance"], "Enfermés dans un camp de concentration nazi, un père juif use d'imagination et d'humour pour masquer l'horreur à son jeune fils."),
    ("tt0245429", "Spirited Away (Le Voyage de Chihiro)", "2001", "8.6", ["Animation", "Adventure", "Family", "Fantasy"], "Chihiro, une fillette de dix ans, pénètre dans un monde secret dominé par les esprits et doit travailler dans les bains publics d'une sorcière pour sauver ses parents transformés en porcs."),
    ("tt0120815", "Saving Private Ryan (Il faut sauver le soldat Ryan)", "1998", "8.6", ["Drama", "War"], "Après le débarquement en Normandie, une escouade de soldats américains part derrière les lignes ennemies pour retrouver un soldat dont les trois frères sont morts au combat."),
    ("tt0105236", "Reservoir Dogs", "1992", "8.3", ["Crime", "Thriller"], "Après l'échec sanglant d'un braquage de diamants, six criminels qui ne se connaissent que par des noms de code réalisent qu'une taupe est parmi eux."),
    ("tt0076759", "Star Wars: Episode IV - A New Hope", "1977", "8.6", ["Action", "Adventure", "Fantasy", "Sci-Fi"], "Luke Skywalker s'allie à un chevalier Jedi, un pilote arrogant et deux droïdes pour sauver la galaxie de la terrifiante Étoile de la Mort."),
    ("tt0172495", "Gladiator", "2000", "8.5", ["Action", "Adventure", "Drama"], "Trahis et réduits en esclavage, un ancien général romain devient gladiateur pour venger le massacre de sa famille et défier l'empereur corrompu."),
    ("tt0114814", "The Usual Suspects", "1995", "8.5", ["Crime", "Drama", "Mystery", "Thriller"], "L'unique survivant d'un massacre sur un cargo raconte à la police comment un mystérieux baron du crime nommé Keyser Söze a orchestré l'affaire."),
    ("tt0110413", "Léon: The Professional", "1994", "8.5", ["Action", "Crime", "Drama"], "Léon, un tueur à gages solitaire à New York, prend sous son aile Mathilda, une fillette de douze ans dont la famille a été assassinée par un flic corrompu."),
    ("tt0103064", "Terminator 2: Judgment Day", "1991", "8.6", ["Action", "Sci-Fi"], "Un cyborg reprogrammé est envoyé du futur pour protéger un jeune garçon destiné à mener la résistance humaine contre les machines."),
    ("tt0088763", "Back to the Future (Retour vers le futur)", "1985", "8.5", ["Adventure", "Comedy", "Sci-Fi"], "Marty McFly est propulsé accidentellement en 1955 au volant d'une DeLorean modifiée par son ami inventeur Doc Brown."),
    ("tt0054215", "Psycho (Psychose)", "1960", "8.5", ["Horror", "Mystery", "Thriller"], "Une secrétaire en fuite avec une forte somme d'argent fait étape dans un motel isolé tenu par Norman Bates, un jeune homme sous l'emprise de sa mère."),
    ("tt0034583", "Casablanca", "1942", "8.5", ["Drama", "Romance", "War"], "Pendant la Seconde Guerre mondiale à Casablanca, le propriétaire d'un club huppé doit choisir entre son amour de jeunesse et la lutte contre le nazisme."),
    ("tt0027977", "Modern Times (Les Temps modernes)", "1936", "8.5", ["Comedy", "Drama", "Romance"], "Charlot se débat avec la cadence infernale du travail à la chaîne dans une société industrielle en pleine crise économique."),
    ("tt0064116", "Once Upon a Time in the West (Il était une fois dans l'Ouest)", "1968", "8.5", ["Western"], "Une veuve, un hors-la-loi au grand cœur et un mystérieux joueur d'harmonica s'unissent contre un tueur impitoyable à la solde du chemin de fer."),
    ("tt0046949", "Rear Window (Fenêtre sur cour)", "1954", "8.5", ["Mystery", "Thriller"], "Immobilisé chez lui par une jambe plâtrée, un photographe observe ses voisins d'en face et en vient à soupçonner l'un d'eux de meurtre."),
    ("tt0078748", "Alien (Le Huitième Passager)", "1979", "8.5", ["Horror", "Sci-Fi"], "L'équipage du remorqueur spatial Nostromo répond à un signal de détresse et embarque à son insu une créature extraterrestre prédatrice."),
    ("tt0093779", "Full Metal Jacket", "1987", "8.3", ["Drama", "War"], "L'entraînement impitoyable de jeunes recrues des Marines sous la houlette d'un sergent instructeur sadique, avant d'être plongés dans l'enfer de la guerre du Viêt Nam."),
    ("tt0075314", "Taxi Driver", "1976", "8.2", ["Crime", "Drama"], "Un vétéran du Viêt Nam solitaire et insomniaque conduit un taxi de nuit à New York et sombre peu à peu dans une spirale paranoïaque et violente."),
    ("tt0066765", "A Clockwork Orange (Orange mécanique)", "1971", "8.3", ["Crime", "Sci-Fi"], "Dans une Angleterre futuriste gangrenée par la délinquance, le chef d'une bande d'adolescents ultraviolents subit une thérapie expérimentale de conditionnement psychologique."),
    ("tt0084787", "The Thing", "1982", "8.2", ["Horror", "Mystery", "Sci-Fi"], "Dans une base scientifique en Antarctique, une équipe de chercheurs est confrontée à une entité extraterrestre capable d'imiter parfaitement n'importe quel être vivant."),
    ("tt0082971", "Raiders of the Lost Ark (Les Aventuriers de l'arche perdue)", "1981", "8.4", ["Action", "Adventure"], "En 1936, l'archéologue Indiana Jones est engagé par le gouvernement américain pour retrouver l'Arche d'Alliance avant les nazis."),
    ("tt0080678", "The Shining", "1980", "8.4", ["Drama", "Horror"], "Engagé comme gardien d'hiver d'un hôtel de montagne isolé, un écrivain s'y installe avec sa famille tandis que des forces sinistres le font basculer dans la folie."),
    ("tt0078788", "Apocalypse Now", "1979", "8.4", ["Drama", "Mystery", "War"], "Pendant la guerre du Viêt Nam, un capitaine américain est chargé d'une mission secrète : remonter un fleuve jusqu'au Cambodge pour éliminer un colonel renégat."),
    ("tt0062622", "2001: A Space Odyssey (2001 : L'Odyssée de l'espace)", "1968", "8.3", ["Adventure", "Sci-Fi"], "De l'aube de l'humanité à un voyage vers Jupiter, deux astronautes et l'ordinateur de bord tout-puissant HAL 9000 découvrent un mystérieux monolithe noir."),
    ("tt0120586", "American History X", "1998", "8.5", ["Crime", "Drama"], "Un ancien leader néonazi tente d'empêcher son jeune frère de suivre la même voie de haine et de violence après sa sortie de prison."),
    ("tt0109506", "The Lion King (Le Roi Lion)", "1994", "8.5", ["Animation", "Adventure", "Drama"], "Le lionceau Simba, héritier du trône de la savane, doit surmonter la culpabilité de la mort de son père pour reconquérir son royaume des mains de son oncle Scar."),
    ("tt0111282", "Stargate", "1994", "7.0", ["Action", "Adventure", "Sci-Fi"], "La découverte d'un anneau de pierre antique en Égypte ouvre un passage interstellaire vers un monde dominé par un faux dieu extraterrestre."),
    ("tt0095016", "Die Hard (Piège de cristal)", "1988", "8.2", ["Action", "Thriller"], "Le flic new-yorkais John McClane se retrouve seul face à un commando de terroristes qui a pris d'assaut un gratte-ciel de Los Angeles le soir de Noël."),
    ("tt0086250", "Scarface", "1983", "8.3", ["Crime", "Drama"], "Réfugié cubain débarqué à Miami, Tony Montana gravit impitoyablement les échelons du trafic de cocaïne jusqu'au sommet du cartel."),
    ("tt0079588", "Mad Max", "1979", "6.8", ["Action", "Adventure", "Sci-Fi"], "Dans une Australie post-apocalyptique en ruines, un policier venge le massacre de sa femme et de son enfant par une bande de motards sanguinaires."),
    ("tt0097576", "Indiana Jones and the Last Crusade", "1989", "8.2", ["Action", "Adventure"], "Indiana Jones part à la recherche de son père disparu, lui-même lancé sur la piste du légendaire Saint Graal convoité par les nazis."),
    ("tt0119217", "Good Will Hunting", "1997", "8.3", ["Drama", "Romance"], "Un jeune agent d'entretien doué d'un génie mathématique extraordinaire surmonte ses traumatismes d'enfance avec l'aide d'un thérapeute bienveillant."),
    ("tt0112573", "Braveheart", "1995", "8.3", ["Biography", "Drama", "History", "War"], "Au XIIIe siècle, William Wallace prend la tête de la rébellion écossaise contre la tyrannie du roi d'Angleterre Édouard Ier."),
    ("tt0084827", "The Untouchables (Les Incorruptibles)", "1987", "7.8", ["Crime", "Drama", "Thriller"], "À Chicago pendant la Prohibition, l'agent fédéral Eliot Ness forme une brigade de flics intègres pour faire tomber le caïd Al Capone."),
    ("tt0120689", "The Green Mile (La Ligne verte)", "1999", "8.6", ["Crime", "Drama", "Fantasy"], "Les gardiens d'un couloir de la mort dans les années 1930 découvrent qu'un condamné colossal possède un pouvoir de guérison miraculeux."),
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


IMDB_ID_ALIASES = {
    "tt22248374": "tt22248376",  # Frieren: Beyond Journey's End (Cinemeta / IMDb)
    "tt1370601": "tt1355642",    # Fullmetal Alchemist: Brotherhood (Cinemeta / IMDb)
}

ANIME_IMAGE_FIXES = {
    "tt22248376": {
        "poster": "https://media.kitsu.app/anime/46474/poster_image/large-ec9b98dd5fbf8f92532d1edb45f9e882.jpeg",
        "background": "https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg",
        "cover": "https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg",
    },
    "tt1355642": {
        "poster": "https://media.kitsu.app/anime/3936/poster_image/large-a94f61b0c0f8623b371cf78696ecdd44.jpeg",
        "background": "https://media.kitsu.app/anime/cover_images/3936/large.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/3936/large.jpg",
    },
    "tt21209876": {
        "poster": "https://media.kitsu.app/anime/46231/poster_image/large-22cba102be8d90ca0d6eec4d57cff5b6.jpeg",
        "background": "https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg",
        "cover": "https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg",
    },
}

CINEMETA_FALLBACK_ANIMES = [
    {"id": "tt2560140", "type": "series", "name": "Attack on Titan", "year": "2013", "releaseInfo": "2013-2023", "imdbRating": "9.1", "poster": "https://images.metahub.space/poster/medium/tt2560140/img", "background": "https://images.metahub.space/background/medium/tt2560140/img", "genres": ["Animation", "Action", "Adventure"], "description": "Dans un monde où les humains vivent enfermés dans des cités entourées de gigantesques remparts pour se protéger de créatures colossales nommées Titans, le jeune Eren Jaeger jure d'éradiquer ces prédateurs."},
    {"id": "tt12343534", "type": "series", "name": "Jujutsu Kaisen", "year": "2020", "releaseInfo": "2020-", "imdbRating": "8.5", "poster": "https://images.metahub.space/poster/medium/tt12343534/img", "background": "https://images.metahub.space/background/medium/tt12343534/img", "genres": ["Animation", "Action", "Fantasy"], "description": "Yuji Itadori, lycéen aux aptitudes physiques exceptionnelles, avale une relique maudite de rang S pour sauver ses amis et se retrouve possédé par Ryomen Sukuna, le Roi des Fléaux."},
    {"id": "tt9335498", "type": "series", "name": "Demon Slayer: Kimetsu no Yaiba", "year": "2019", "releaseInfo": "2019-", "imdbRating": "8.6", "poster": "https://images.metahub.space/poster/medium/tt9335498/img", "background": "https://images.metahub.space/background/medium/tt9335498/img", "genres": ["Animation", "Action", "Fantasy"], "description": "Après le massacre de sa famille par un démon et la transformation de sa jeune sœur Nezuko, Tanjiro Kamado devient pourfendeur de démons pour la délivrer de cette malédiction."},
    {"id": "tt0388629", "type": "series", "name": "One Piece", "year": "1999", "releaseInfo": "1999-", "imdbRating": "9.0", "poster": "https://images.metahub.space/poster/medium/tt0388629/img", "background": "https://images.metahub.space/background/medium/tt0388629/img", "genres": ["Animation", "Action", "Adventure"], "description": "Monkey D. Luffy prend la mer à la recherche du trésor légendaire, le One Piece, avec l'ambition suprême de devenir le Roi des Pirates."},
    {"id": "tt22248376", "type": "series", "name": "Frieren: Beyond Journey's End", "year": "2023", "releaseInfo": "2023-", "imdbRating": "8.9", "poster": "https://media.kitsu.app/anime/46474/poster_image/large-ec9b98dd5fbf8f92532d1edb45f9e882.jpeg", "background": "https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg", "cover": "https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg", "genres": ["Animation", "Adventure", "Drama"], "description": "Après la défaite du Roi Démon par le groupe de héros, l'elfe magicienne Frieren entame un nouveau voyage pour comprendre la valeur éphémère du temps et des liens humains."},
    {"id": "tt21209876", "type": "series", "name": "Solo Leveling", "year": "2024", "releaseInfo": "2024-", "imdbRating": "8.3", "poster": "https://media.kitsu.app/anime/46231/poster_image/large-22cba102be8d90ca0d6eec4d57cff5b6.jpeg", "background": "https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg", "cover": "https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg", "genres": ["Animation", "Action", "Fantasy"], "description": "Sung Jinwoo, le chasseur le plus faible du monde, reçoit la capacité unique d'évoluer sans limite via une interface de jeu invisible."},
    {"id": "tt0877057", "type": "series", "name": "Death Note", "year": "2006", "releaseInfo": "2006-2007", "imdbRating": "8.9", "poster": "https://images.metahub.space/poster/medium/tt0877057/img", "background": "https://images.metahub.space/background/medium/tt0877057/img", "genres": ["Animation", "Crime", "Drama"], "description": "Light Yagami, brillant lycéen, trouve un carnet surnaturel permettant de tuer quiconque dont on connaît le nom et le visage."},
    {"id": "tt1355642", "type": "series", "name": "Fullmetal Alchemist: Brotherhood", "year": "2009", "releaseInfo": "2009-2010", "imdbRating": "9.1", "poster": "https://media.kitsu.app/anime/3936/poster_image/large-a94f61b0c0f8623b371cf78696ecdd44.jpeg", "background": "https://media.kitsu.app/anime/cover_images/3936/large.jpg", "cover": "https://media.kitsu.app/anime/cover_images/3936/large.jpg", "genres": ["Animation", "Action", "Adventure"], "description": "Edward et Alphonse Elric parcourent le monde à la recherche de la Pierre Philosophale pour restaurer leurs corps perdus."},
    {"id": "tt2098220", "type": "series", "name": "Hunter x Hunter", "year": "2011", "releaseInfo": "2011-2014", "imdbRating": "9.0", "poster": "https://images.metahub.space/poster/medium/tt2098220/img", "background": "https://images.metahub.space/background/medium/tt2098220/img", "genres": ["Animation", "Action", "Adventure"], "description": "Gon Freecss décide de passer le redoutable examen de Hunter dans l'espoir de retrouver son père Ging, l'un des Hunters les plus mystérieux au monde."},
    {"id": "tt13616990", "type": "series", "name": "Chainsaw Man", "year": "2022", "releaseInfo": "2022-", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt13616990/img", "background": "https://images.metahub.space/background/medium/tt13616990/img", "genres": ["Animation", "Action", "Horror"], "description": "Denji, jeune homme criblé de dettes vivant avec son démon-tronçonneuse Pochita, fusionne avec ce dernier pour devenir Chainsaw Man."},
    {"id": "tt0434665", "type": "series", "name": "Bleach", "year": "2004", "releaseInfo": "2004-2012", "imdbRating": "8.2", "poster": "https://images.metahub.space/poster/medium/tt0434665/img", "background": "https://images.metahub.space/background/medium/tt0434665/img", "genres": ["Animation", "Action", "Adventure"], "description": "Ichigo Kurosaki, adolescent capable de voir les esprits, devient Shinigami pour défendre les humains contre les monstres Hollows."},
    {"id": "tt0409591", "type": "series", "name": "Naruto", "year": "2002", "releaseInfo": "2002-2007", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt0409591/img", "background": "https://images.metahub.space/background/medium/tt0409591/img", "genres": ["Animation", "Action", "Adventure"], "description": "Naruto Uzumaki, jeune ninja orphelin porteur du Démon-Renard à neuf queues, rêve de devenir Hokage pour être enfin reconnu par tous."},
    {"id": "tt0988824", "type": "series", "name": "Naruto: Shippuden", "year": "2007", "releaseInfo": "2007-2017", "imdbRating": "8.7", "poster": "https://images.metahub.space/poster/medium/tt0988824/img", "background": "https://images.metahub.space/background/medium/tt0988824/img", "genres": ["Animation", "Action", "Adventure"], "description": "Deux ans et demi après son départ, Naruto revient à Konoha plus fort que jamais face à la menace grandissante de l'Akatsuki."},
    {"id": "tt0245429", "type": "movie", "name": "Le Voyage de Chihiro", "year": "2001", "releaseInfo": "2001", "imdbRating": "8.6", "poster": "https://images.metahub.space/poster/medium/tt0245429/img", "background": "https://images.metahub.space/background/medium/tt0245429/img", "genres": ["Animation", "Adventure", "Family"], "description": "Chihiro, une fillette de dix ans, s'aventure dans un parc à thème abandonné qui s'avère être un monde enchanté peuplé d'esprits."},
    {"id": "tt5311514", "type": "movie", "name": "Your Name.", "year": "2016", "releaseInfo": "2016", "imdbRating": "8.4", "poster": "https://images.metahub.space/poster/medium/tt5311514/img", "background": "https://images.metahub.space/background/medium/tt5311514/img", "genres": ["Animation", "Drama", "Fantasy"], "description": "Mitsuha, lycéenne dans un village rural, et Taki, lycéen à Tokyo, découvrent qu'ils échangent mystérieusement de corps pendant leur sommeil."},
    {"id": "tt0119698", "type": "movie", "name": "Princesse Mononoké", "year": "1997", "releaseInfo": "1997", "imdbRating": "8.3", "poster": "https://images.metahub.space/poster/medium/tt0119698/img", "background": "https://images.metahub.space/background/medium/tt0119698/img", "genres": ["Animation", "Action", "Adventure"], "description": "Frappé d'une malédiction, le jeune guerrier Ashitaka quitte son village et se retrouve pris dans une guerre sanglante entre les dieux de la forêt et les humains."}
]

KNOWN_ANIME_IDS = {
    # Core top anime series & films
    "tt2560140", "tt12343534", "tt9335498", "tt0388629", "tt22248376",
    "tt21209876", "tt0877057", "tt1355642", "tt2098220", "tt13616990",
    "tt0434665", "tt0409591", "tt0988824", "tt0245429", "tt5311514", "tt0119698",
    # Additional top anime from Cinemeta genre=Anime catalog
    "tt13293588", "tt5607616", "tt5626028", "tt10233448", "tt9054364",
    "tt0434706", "tt12590266", "tt2359704", "tt7441658", "tt0318871",
    "tt0994314", "tt37532356", "tt37614297", "tt4508902", "tt0112159",
    "tt26743760", "tt1910272", "tt0988818", "tt5897304", "tt30217403",
    "tt39551330", "tt37532893", "tt36517689", "tt0168366", "tt32550889",
    "tt3741634", "tt28618556", "tt3398540", "tt13911284", "tt21975436",
    "tt13718450", "tt9679542", "tt4644488", "tt3895150", "tt13706018",
    "tt15222080", "tt7263328", "tt7078180", "tt33334216", "tt5249462",
    "tt32612521", "tt15765670", "tt3358020", "tt0948103", "tt38939446",
    "tt7222086", "tt36988358", "tt9522300", "tt36592690", "tt13196080",
    "tt2230051", "tt14976292", "tt14115938", "tt2250192", "tt36034547",
    "tt9307686", "tt0421357", "tt32869308", "tt9458304", "tt26737616",
    "tt0423731", "tt0481256", "tt17069148", "tt0500092", "tt3909224",
    "tt21621494", "tt28919914", "tt41293157", "tt2379308", "tt13103134",
    "tt33044444", "tt0096633", "tt32991344", "tt21030032", "tt33028568",
    "tt39304754", "tt2404499", "tt0131179", "tt8086718", "tt32536168",
    "tt0810705", "tt11147852", "tt8788458", "tt40548519", "tt1118804",
    "tt39123061", "tt0088509", "tt0092455", "tt0099685", "tt0078638",
    "tt0094583", "tt0103442", "tt0112108", "tt0202206"
}


def is_anime_item(m):
    """Détermine avec certitude si un média est un animé japonais."""
    if not m or not isinstance(m, dict):
        return False
    if m.get("is_anime"):
        return True
    mid = m.get("id")
    if mid and mid in KNOWN_ANIME_IDS:
        return True
    genres = [str(g).lower() for g in (m.get("genres") or [])]
    if "anime" in genres:
        return True
    return False


def get_catalog_top(media_type="movie", genre="", skip=0, sort="top"):
    genre = (genre or "").strip()
    sort = (sort or "top").strip()
    skip = max(0, int(skip or 0))
    if media_type == "classics":
        all_classics = get_classics_catalog(genre=genre, sort=sort)
        return all_classics[skip:] if skip > 0 else all_classics
    if media_type == "anime":
        cache_key = f"catalog:anime_v4:{sort}:{genre or 'all'}:{skip}"

        def _fetch_anime():
            metas = anime_engine.get_anime_catalog(genre=genre, skip=skip, sort=sort)
            for m in metas:
                mid = m.get("id")
                if mid:
                    KNOWN_ANIME_IDS.add(mid)
            if metas:
                try:
                    threading.Thread(target=kino_db.db_index_media, args=(metas,), daemon=True).start()
                except Exception:
                    pass
            return metas

        return cached_get(cache_key, 600, _fetch_anime)

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

        if media_type == "series" and genre.lower() not in ("anime", "animation"):
            metas = [m for m in metas if not is_anime_item(m)]
            if len(metas) < 35:
                try:
                    extra_next = f"/{catalog_id}/skip={skip + 50}.json"
                    url_next = f"https://v3-cinemeta.strem.io/catalog/{media_type}{extra_next}"
                    data_next = http_json(url_next)
                    for nm in data_next.get("metas", []):
                        if not is_anime_item(nm) and nm.get("id") not in {x.get("id") for x in metas}:
                            metas.append(nm)
                except Exception:
                    pass

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
    imdb_id = IMDB_ID_ALIASES.get(imdb_id, imdb_id)
    if media_type == "anime":
        known_type = kino_db.db_get_media_type(imdb_id)
        if known_type in ("movie", "series"):
            res = get_media_meta(imdb_id, known_type)
            if res and res.get("name"):
                res["is_anime"] = True
                return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_series = ex.submit(get_media_meta, imdb_id, "series")
            f_movie = ex.submit(get_media_meta, imdb_id, "movie")
            s_meta = f_series.result()
            if s_meta and s_meta.get("name") and s_meta.get("videos"):
                s_meta["type"] = "series"
                s_meta["is_anime"] = True
                return s_meta
            m_meta = f_movie.result()
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
        if not meta:
            return {}

        if imdb_id in ANIME_IMAGE_FIXES:
            for k, v in ANIME_IMAGE_FIXES[imdb_id].items():
                if v and (not meta.get(k) or "metahub.space" in str(meta.get(k))):
                    meta[k] = v

        # Cinemeta sometimes keeps HTML entities in names (« Catherine O&apos;Hara »).
        for key in ("name", "description", "awards"):
            if isinstance(meta.get(key), str):
                meta[key] = html.unescape(meta[key])
        for key in ("cast", "director", "writer"):
            if isinstance(meta.get(key), list):
                meta[key] = [html.unescape(str(value)) for value in meta[key]]

        raw_genres = meta.get("genres") or meta.get("genre") or []
        if isinstance(raw_genres, list):
            meta["genres_fr"] = [GENRE_FR_MAP.get(g, g) for g in raw_genres]

        # 1. Suggestions similaires instantanees depuis le catalogue local (< 5ms)
        similar = kino_db.db_get_similar(imdb_id, media_type=media_type, genres=raw_genres, limit=8)
        if len(similar) < 4 and raw_genres and isinstance(raw_genres, list):
            try:
                pool = get_catalog_top(media_type, genre=raw_genres[0], skip=0, sort="top") or []
                seen_ids = {imdb_id}.union(s.get("id") for s in similar)
                for cand in pool:
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
                        if len(similar) >= 8:
                            break
            except Exception:
                pass
        meta["similar"] = similar[:8]

        # 2. Traduction optimisee du synopsis
        if meta.get("description"):
            meta["description_fr"] = translate_text_fr(meta["description"])

        # 3. Indexation locale en arriere-plan pour accelerer les prochaines recherches
        try:
            threading.Thread(target=kino_db.db_index_media, args=([meta],), daemon=True).start()
        except Exception:
            pass

        # 4. Traitement des series et dernier episode diffuse
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
                        aired_eps.append((s_num, e_num, rel, v.get("name") or v.get("title") or f"Episode {e_num}"))
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

    return cached_get(cache_key, 3600, _fetch)


def related_titles(imdb_id, media_type="movie"):
    """Titles close to this one: TMDB's recommendations with the user's key
   , else KINO's own suggestions, else the genre match."""
    import tmdb
    key = load_config().get("tmdb_key") or ""
    if key and re.fullmatch(r"tt\d{1,12}", str(imdb_id or "")):
        try:
            items = tmdb.related(imdb_id, media_type, key, cache=cached_get)
            if len(items) >= 4:
                return {"source": "tmdb", "items": items}
        except (tmdb.TmdbError, OSError, ValueError):
            pass
    try:
        items = kino_related(imdb_id, media_type)
        if len(items) >= 4:
            return {"source": "kino", "items": items}
    except Exception:
        pass
    meta = get_media_meta(imdb_id, media_type) or {}
    return {"source": "genres", "items": list(meta.get("similar") or [])}


# --- KINO's own suggestions (recommender.py) ----------------------
_SUGGESTIONS = {"store": None, "building": False}
POOL_READY = 500


def suggestion_store():
    import recommender
    if _SUGGESTIONS["store"] is None:
        _SUGGESTIONS["store"] = recommender.Store(kino_db.get_connection, kino_db._DB_LOCK)
    return _SUGGESTIONS["store"]


def refresh_suggestion_pool(force=False):
    """Reads Cinemeta's public catalogues again when the pool is empty or a month old."""
    import recommender
    store = suggestion_store()
    count, age = store.pool_age(time.time())
    if not force and count >= POOL_READY and age is not None and age < recommender.POOL_MAX_AGE:
        return count
    if _SUGGESTIONS["building"]:
        return count
    _SUGGESTIONS["building"] = True
    try:
        entries = recommender.build_pool(lambda url: http_json(url, timeout=12, retries=1))
        if len(entries) >= POOL_READY:
            store.save_pool(entries, time.time())
            return len(entries)
        return count
    finally:
        _SUGGESTIONS["building"] = False


def _known_titles(space, ids, kind_of):
    """The user's titles as pool entries; those missing are read once (public record)."""
    import recommender
    known = {i: space.entries[i] for i in ids if i in space.entries}
    missing = [i for i in ids if i not in known][:40]
    if missing:
        def read(item_id):
            meta = get_media_meta(item_id, kind_of.get(item_id) or "movie") or {}
            return recommender.compact({**meta, "imdb_id": item_id}, kind_of.get(item_id) or "movie") if meta.get("name") else None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            fetched = [entry for entry in pool.map(read, missing) if entry]
        if fetched:
            suggestion_store().add_known(fetched, time.time())
            space = suggestion_store().space()
            known.update({e["id"]: e for e in fetched})
    return known


def _user_state():
    history = kino_db.db_get_history()
    watchlist = kino_db.db_get_watchlist()
    kind_of = {str(e.get("id") or ""): ("series" if e.get("type") in ("series", "anime") else "movie") for e in history + watchlist}
    return history, watchlist, kind_of


def kino_for_you(media_type="movie", limit=20):
    """« Pour vous »: {ready, items, pool} for this tab's type."""
    import recommender
    store = suggestion_store()
    space = store.space()
    if len(space.entries) < POOL_READY:
        return {"ready": False, "items": [], "pool": len(space.entries)}
    history, watchlist, kind_of = _user_state()
    signals = recommender.taste_signals(history, watchlist, store.not_for_me(), time.time())
    known = _known_titles(space, list(signals), kind_of)
    space = store.space()
    kind = "series" if media_type in ("series", "anime") else "movie"
    items = recommender.for_you(space, signals, known, kind, limit=limit)
    return {"ready": True, "items": items, "pool": len(space.entries)}


def kino_related(imdb_id, media_type="movie", limit=12):
    """Titles close to one title, by KINO's own traits (people, genres, era, themes)."""
    import recommender
    store = suggestion_store()
    space = store.space()
    if len(space.entries) < POOL_READY:
        return []
    kind = "series" if media_type in ("series", "anime") else "movie"
    source = _known_titles(space, [imdb_id], {imdb_id: kind}).get(imdb_id)
    history, _, _ = _user_state()
    seen = {str(e.get("id") or "") for e in history if e.get("completed") or e.get("imported_watched")}
    return recommender.related(store.space(), source, kind, exclude=seen | set(store.not_for_me()), limit=limit)


# Cast photos for the immersive fiche: Wikipedia summaries need no key. Only a
# page describing someone from film or TV is accepted, so a namesake (a
# politician, a town) never lends its picture to an actor.
_SCREEN_PERSON = re.compile(
    r"\b(actor|actress|voice|comedian|composer|director|filmmaker|screenwriter|writer|producer|singer|"
    r"musician|rapper|model|presenter|television|entertainer|performer|dancer|animator|youtuber)\b", re.IGNORECASE)
WIKIPEDIA_HEADERS = {"User-Agent": "KINO/1.1 (lecteur de films local ; photos de la distribution)"}
PERSON_PHOTO_TTL = 30 * 86400


def screen_person_summary(name):
    """The English Wikipedia summary of a person of film or TV (a dict), '' when
    there is none, None on network trouble. Also used by the actor page."""
    for title in (name, f"{name} (actor)", f"{name} (actress)"):
        url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"), safe="")
        try:
            data = http_json(url, headers=WIKIPEDIA_HEADERS, timeout=6, retries=1)
        except RuntimeError as error:
            if "(404)" in str(error):
                continue
            return None  # not cached: try again later
        if data.get("type") != "standard" or not _SCREEN_PERSON.search(str(data.get("description") or "")):
            continue
        return data
    return ""


def person_photo(name):
    """Wikipedia thumbnail URL for a cast or crew member, '' when none, None on network trouble."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not name:
        return ""

    def fetch():
        data = screen_person_summary(name)
        if not isinstance(data, dict):
            return data
        thumb = str((data.get("thumbnail") or {}).get("source") or "")
        parts = urllib.parse.urlsplit(thumb)
        wikimedia = parts.scheme == "https" and (parts.hostname or "").endswith(".wikimedia.org")
        return thumb if wikimedia else ""

    return cached_get(f"person_photo_v1:{name.lower()}", PERSON_PHOTO_TTL, fetch)


def person_photos(names):
    """Photos for up to 12 names, looked up in parallel: {name: url or None}."""
    unique = []
    for name in names or []:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if name and name not in unique:
            unique.append(name)
    unique = unique[:12]
    if not unique:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        photos = list(pool.map(person_photo, unique))
    return {name: (photo or None) for name, photo in zip(unique, photos)}


# French titles: the English Wikipedia page of the work (checked by year and
# kind) links to the French page, whose title is the French release title.
# Wikidata would match by IMDb id but its services are rate-limited.
FRENCH_TITLE_TTL = 30 * 86400
_FR_TITLE_SUFFIX = re.compile(
    r"\s*\((?:film|série télévisée|série d'animation|téléfilm|série|mini-série|feuilleton)[^)]*\)$", re.IGNORECASE)
_SERIES_DESCRIPTION = re.compile(r"television|\bTV\b|anime|web series|sitcom|miniseries", re.IGNORECASE)
_NOT_SCREEN_WORK = re.compile(r"manga|novel|comic|video game|book", re.IGNORECASE)


def _french_title_candidates(name, year, kind):
    if kind == "series":
        return [f"{name} ({year} TV series)", f"{name} (TV series)", f"{name} (American TV series)", name]
    return [f"{name} ({year} film)", f"{name} (film)", name]


def _description_matches(description, year, kind):
    if _NOT_SCREEN_WORK.search(description or ""):
        return False
    if kind == "series":
        return bool(_SERIES_DESCRIPTION.search(description or ""))
    years = {int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", description or "")}
    # Without its year a listing cannot be told from the famous film of the same name.
    return bool(re.search(r"\bfilm\b", description or "", re.IGNORECASE)) and bool(year) and (
        any(abs(found - int(year)) <= 1 for found in years))


def _wikipedia_french_titles(items):
    """{id: French title or ''} for up to 12 works in one request; None when Wikipedia does not answer."""
    titles = [c for item in items for c in _french_title_candidates(item["name"], item["year"], item["kind"])]
    params = {"action": "query", "titles": "|".join(titles), "prop": "langlinks|description", "lllang": "fr",
              "lllimit": "max", "redirects": 1, "format": "json", "formatversion": 2}
    try:
        data = http_json("https://en.wikipedia.org/w/api.php", method="POST", data=params,
                         headers=WIKIPEDIA_HEADERS, timeout=10, retries=1)
    except Exception:
        return None
    query = data.get("query") or {}
    normalized = {n.get("from"): n.get("to") for n in query.get("normalized", [])}
    redirects = {r.get("from"): r.get("to") for r in query.get("redirects", [])}
    pages = {p.get("title"): p for p in query.get("pages", [])}
    found = {}
    for item in items:
        found[item["id"]] = ""
        for candidate in _french_title_candidates(item["name"], item["year"], item["kind"]):
            title = normalized.get(candidate, candidate)
            page = pages.get(redirects.get(title, title))
            if not page or page.get("missing") or not _description_matches(page.get("description"), item["year"], item["kind"]):
                continue
            links = page.get("langlinks") or []
            if links and links[0].get("title"):
                found[item["id"]] = _FR_TITLE_SUFFIX.sub("", links[0]["title"]).strip()
            break
    return found


_FRENCH_WORDS = re.compile(r"(?:^|\s)(?:le|la|les|l'|l’|un|une|des|du|de|et|au|aux)(?:\s|$)|[àâçéèêëîïôûùüÿœ]", re.IGNORECASE)


def split_french_alias(name):
    """« Spirited Away (Le Voyage de Chihiro) » → ('Spirited Away', 'Le Voyage de Chihiro').

    KINO's own catalogue keeps some French titles in parentheses. A parenthesis
    that is a year, or not French (« Birdman (or The Unexpected…) »), stays in the name.
    """
    match = re.fullmatch(r"(.+?)\s*\(([^()]{2,120})\)", name or "")
    if match and not re.fullmatch(r"[\d\s–-]+", match.group(2)) and _FRENCH_WORDS.search(match.group(2)):
        return match.group(1).strip(), match.group(2).strip()
    return name, ""


def french_titles(items):
    """French display titles for works identified by IMDb id: {id: title, or '' when none is known}."""
    clean = []
    for item in (items or [])[:60]:
        if not isinstance(item, dict):
            continue
        media_id = str(item.get("id") or "")
        name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()[:200]
        year = (re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(item.get("year") or "")) or [""])[0]
        if re.fullmatch(r"tt\d{1,12}", media_id) and name:
            name, alias = split_french_alias(name)
            clean.append({"id": media_id, "name": name, "year": year, "alias": alias,
                          "kind": "series" if item.get("type") in ("series", "anime") else "movie"})
    result, missing = {}, []
    for item in clean:
        if item["alias"]:
            result[item["id"]] = item["alias"]
            continue
        cached = kino_db.db_cache_get(f"title_fr_v1:{item['id']}")
        if cached is not None:
            result[item["id"]] = cached
        elif item["id"] not in {m["id"] for m in missing}:
            missing.append(item)
    for start in range(0, len(missing), 12):
        batch = missing[start:start + 12]
        found = _wikipedia_french_titles(batch)
        if found is None:
            continue  # not cached: asked again next time
        for item in batch:
            title = found.get(item["id"], "")
            kino_db.db_cache_set(f"title_fr_v1:{item['id']}", title, ttl_sec=FRENCH_TITLE_TTL)
            result[item["id"]] = title
    # Known French titles also feed the local search (« Les Évadés » → The Shawshank Redemption).
    refused = kino_db.db_set_aliases([
        {"id": item["id"], "alias": result[item["id"]], "name": item["name"], "year": item["year"], "type": item["kind"]}
        for item in clean
        if result.get(item["id"]) and result[item["id"]].casefold() != item["name"].casefold()
    ]) or set()
    # Another listing of the same work holds that title: this one keeps its own name.
    for media_id in refused:
        result[media_id] = ""
        kino_db.db_cache_set(f"title_fr_v1:{media_id}", "", ttl_sec=FRENCH_TITLE_TTL)
    return result


# --- Original titles ---------------------------------------------------------
# KINO shows each work under its original title, never a translation: « The
# Shawshank Redemption », « Der Untergang », « La haine ». Wikidata gives it by IMDb
# id (property P1476), free and without a key. A title in another alphabet
# (Japanese, Korean…) is not shown: the catalogue's international name stays.
ORIGINAL_TITLE_TTL = 180 * 86400
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_AGENT = "KINO (https://github.com/isleepverywellatnight/kino)"


def is_latin_title(title):
    return all(not ch.isalpha() or ch <= "\u024f" or "\u1e00" <= ch <= "\u1eff" for ch in title)


def _wikidata_original_titles(ids):
    """{imdb id: original title} for these ids, or None when Wikidata cannot be asked."""
    values = " ".join(f'"{media_id}"' for media_id in ids)
    query = f"SELECT ?imdb ?title WHERE {{ VALUES ?imdb {{ {values} }} ?work wdt:P345 ?imdb; wdt:P1476 ?title. }}"
    url = f"{WIKIDATA_SPARQL}?{urllib.parse.urlencode({'query': query, 'format': 'json'})}"
    try:
        data = http_json(url, headers={"Accept": "application/sparql-results+json", "User-Agent": _WIKIDATA_AGENT},
                         timeout=12, retries=1)
    except Exception:
        return None
    found = {}
    for row in (data or {}).get("results", {}).get("bindings", []):
        media_id = row.get("imdb", {}).get("value", "")
        title = re.sub(r"\s+", " ", row.get("title", {}).get("value", "")).strip()
        if media_id and title and media_id not in found:
            found[media_id] = title
    return found


def original_titles(items):
    """Original titles of works identified by IMDb id: {id: title, or '' to keep the catalogue's name}."""
    ids = []
    for item in (items or [])[:60]:
        media_id = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if re.fullmatch(r"tt\d{1,12}", media_id) and media_id not in ids:
            ids.append(media_id)
    result, missing = {}, []
    for media_id in ids:
        cached = kino_db.db_cache_get(f"title_orig_v1:{media_id}")
        if cached is not None:
            result[media_id] = cached
        else:
            missing.append(media_id)
    for start in range(0, len(missing), 60):
        batch = missing[start:start + 60]
        found = _wikidata_original_titles(batch)
        if found is None:
            continue  # not cached: asked again next time
        for media_id in batch:
            title = found.get(media_id, "")
            title = title if title and is_latin_title(title) else ""
            kino_db.db_cache_set(f"title_orig_v1:{media_id}", title, ttl_sec=ORIGINAL_TITLE_TTL)
            result[media_id] = title
    return result


_FR_WORK_DESCRIPTION = re.compile(r"\bfilm\b|série télévisée|série d'animation|téléfilm|anime|mini-série", re.IGNORECASE)
_FRANCHISE = re.compile(r"série de films|série cinématographique|franchise|saga", re.IGNORECASE)
_EN_TITLE_SUFFIX = re.compile(r"\s*\((?:\d{4} )?(?:film|TV series|American TV series|miniseries|franchise|film series)\)$", re.IGNORECASE)


def _comparable(text):
    """Lower case, no accents, no punctuation: « Spider-Man: No Way Home » ≈ « spider man no way home »."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^0-9a-z]+", " ", folded.replace("&", " and ")).strip()


def search_by_french_title(query, media_type="movie"):
    """Works whose French title matches the query, found through French Wikipedia.

    The French page links to the English one; its title is looked up in Cinemeta
    and checked by name and year. Matches are remembered for display and search.
    """
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:120]
    if len(query) < 3:
        return []

    def fetch():
        params = {"action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 6, "gsrnamespace": 0,
                  "prop": "langlinks|description", "lllang": "en", "lllimit": "max", "format": "json", "formatversion": 2}
        try:
            data = http_json("https://fr.wikipedia.org/w/api.php", method="POST", data=params,
                             headers=WIKIPEDIA_HEADERS, timeout=8, retries=1)
        except Exception:
            return None
        works = []
        for page in sorted((data.get("query") or {}).get("pages", []), key=lambda p: p.get("index", 99)):
            description = str(page.get("description") or "")
            links = page.get("langlinks") or []
            if not links or not _FR_WORK_DESCRIPTION.search(description) or _FRANCHISE.search(description):
                continue
            english = _EN_TITLE_SUFFIX.sub("", links[0].get("title") or "").strip()
            years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", f"{description} {links[0].get('title') or ''}")
            kind = "series" if re.search(r"série|anime", description, re.IGNORECASE) else "movie"
            if english:
                works.append({"french": _FR_TITLE_SUFFIX.sub("", page.get("title") or "").strip(), "english": english,
                              "year": years[0] if years else "", "kind": kind})
            if len(works) >= 4:
                break

        def match(work):
            try:
                metas = search_cinemeta(work["english"], work["kind"]) or []
            except Exception:
                return None
            wanted = _comparable(work["english"])
            for meta in metas:
                if _comparable(meta.get("name")) != wanted:
                    continue
                found = re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(meta.get("releaseInfo") or meta.get("year") or ""))
                if work["year"] and found and abs(int(found[0]) - int(work["year"])) > 1:
                    continue
                return dict(meta, name_fr=work["french"], type=meta.get("type") or work["kind"])
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            matches = [m for m in pool.map(match, works) if m and m.get("id")]
        refused = kino_db.db_set_aliases([{"id": m["id"], "alias": m["name_fr"], "name": m.get("name", ""),
                                           "year": str(m.get("releaseInfo") or m.get("year") or ""), "type": m.get("type")}
                                          for m in matches if m["name_fr"].casefold() != str(m.get("name", "")).casefold()]) or set()
        matches = [m for m in matches if m["id"] not in refused]
        for meta in matches:
            kino_db.db_cache_set(f"title_fr_v1:{meta['id']}", meta["name_fr"], ttl_sec=FRENCH_TITLE_TTL)
        return matches

    results = cached_get(f"search_fr_v1:{_comparable(query)}", 86400, fetch) or []
    if media_type in ("movie", "series"):
        results = [m for m in results if m.get("type") == media_type]
    return results


# A subtitle cut for half of a two-CD release covers only half of the film.
_PARTIAL_SUBTITLE = re.compile(r"(?:^|[^a-z0-9])(?:cd|disc|part)[ ._-]*[1-9](?![0-9])", re.IGNORECASE)
_SUBTITLE_FORMATS = (
    ("web", r"web[ ._-]?(?:dl|rip)?|amzn|nf|dsnp|atvp|hmax"),
    ("bluray", r"blu[ ._-]?ray|bdrip|brrip|bdremux|remux"),
    ("hdtv", r"hdtv|pdtv"),
    ("dvd", r"dvd(?:rip|scr)?"),
)
_COMMON_RELEASE_WORDS = {"the", "a", "of", "and", "srt", "mkv", "mp4", "x264", "x265", "h264", "h265", "hevc",
                         "1080p", "720p", "2160p", "480p", "aac", "ac3", "dts", "fr", "fre", "en", "eng"}


def _release_words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - _COMMON_RELEASE_WORDS


def _release_format(text):
    low = (text or "").lower()
    for family, pattern in _SUBTITLE_FORMATS:
        if re.search(rf"(?:^|[^a-z0-9])(?:{pattern})(?:[^a-z0-9]|$)", low):
            return family
    return ""


def subtitle_fit(candidate, release_name="", fps=None):
    """Higher when a subtitle was timed for the release being played; partial (CD1) ones sink."""
    names = f"{candidate.get('file') or ''} {candidate.get('release') or ''}"
    score = -10.0 if _PARTIAL_SUBTITLE.search(names) else 0.0
    if release_name:
        words = _release_words(release_name)
        group = (candidate.get("group") or "").lower()
        if group and group in words:
            score += 4
        family = _release_format(release_name)
        if family and family == (_release_format(candidate.get("format") or "") or _release_format(names)):
            score += 2
        score += min(len(_release_words(names) & words), 6) * 0.25
    # A subtitle timed at 23.976 fps drifts on a 25 fps (PAL) file, and the reverse.
    timed = candidate.get("fps_milli") or 0
    if fps and timed:
        score += 3 if abs(float(fps) * 1000 - timed) <= 60 else -3
    return score


def fetch_opensubtitles(imdb_id, media_type="movie", season=1, episode=1, release_name="", fps=None):
    """Sous-titres français et anglais (addon OpenSubtitles v3), classés pour la release jouée."""
    imdb_id = (imdb_id or "").strip()
    if not imdb_id or not imdb_id.startswith("tt"):
        return []
    if media_type == "series":
        target = f"series/{imdb_id}:{int(season or 1)}:{int(episode or 1)}"
    else:
        target = f"movie/{imdb_id}"
    cache_key = f"opensubs_v2:{target}"

    def _do():
        try:
            url = f"https://opensubtitles-v3.strem.io/subtitles/{target}.json"
            data = http_json(url, timeout=6)
            subs = data.get("subtitles") or []
        except Exception:
            return []

        candidates = []
        seen_urls = set()
        for s in subs:
            u = (s.get("url") or "").strip()
            lg = (s.get("lang") or "").lower()
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            lang = "fr" if lg in ("fre", "fra", "fr") else "en" if lg in ("eng", "en") else ""
            if not lang:
                continue
            file_name = str(s.get("subtitleFileName") or "")
            release = str(s.get("movieReleaseName") or "")
            description = " ".join(str(s.get(key) or "") for key in ("title", "id", "description")).lower()
            description = f"{description} {file_name.lower()} {release.lower()}"
            if s.get("hearing_impaired") is True or re.search(r"\b(sdh|hearing[ ._-]?impaired|malentendants?)\b", description):
                variant = "sdh"
            elif s.get("forced") is True or re.search(r"\b(forced|forc[eé]s?)\b", description):
                variant = "forced"
            else:
                variant = "standard"
            candidates.append({"lang": lang, "url": u, "variant": variant, "file": file_name[:200],
                               "release": release[:200], "group": str(s.get("releaseGroup") or "")[:60],
                               "format": str(s.get("releaseFormat") or "")[:30],
                               "fps_milli": int(s.get("fpsMilli") or 0) if str(s.get("fpsMilli") or "").isdigit() else 0})
        return candidates

    candidates = cached_get(cache_key, 1800, _do) or []
    result = []
    for lang, label, limit in (("fr", "Français", 4), ("en", "English", 3)):
        ranked = sorted(
            ((index, item) for index, item in enumerate(candidates) if item.get("lang") == lang),
            key=lambda pair: (-subtitle_fit(pair[1], release_name, fps), pair[0]),
        )
        for idx, (_index, item) in enumerate(ranked[:limit], 1):
            result.append({
                "id": f"{lang}_{idx}",
                "lang": lang,
                "label": f"{label} #{idx}",
                "variant": item["variant"],
                "partial": bool(_PARTIAL_SUBTITLE.search(f"{item.get('file') or ''} {item.get('release') or ''}")),
                "url": item["url"],
                "vtt_url": f"/api/subtitle.vtt?url={urllib.parse.quote(item['url'])}",
            })
    return result


def srt_to_vtt(srt_text: str) -> str:
    """Convertit un fichier de sous-titres SRT en format WebVTT standard."""
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


def download_top_subtitles_for_mpv(imdb_id, media_type="movie", season=1, episode=1, release_name=""):
    """Télécharge dans le dossier temporaire (/tmp sur Mac) le meilleur sous-titre complet FR et EN pour injection."""
    subs = fetch_opensubtitles(imdb_id, media_type, season, episode, release_name)
    if not subs:
        return []
    picked = []
    for lang_code, fname in (("fr", str(TEMP_DIR / "kino_sub_fr.srt")), ("en", str(TEMP_DIR / "kino_sub_en.srt"))):
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


def _youtube_video_results(html, limit=5):
    """(id, title, seconds) of the first video results of a YouTube search page."""
    match = re.search(r"var ytInitialData = (\{.*?\});</script>", html or "", re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    found = []

    def walk(node):
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict) and renderer.get("videoId"):
                text = "".join(run.get("text", "") for run in (renderer.get("title") or {}).get("runs", []))
                length = (renderer.get("lengthText") or {}).get("simpleText", "")
                seconds = 0
                for part in length.split(":"):
                    seconds = seconds * 60 + int(part) if part.isdigit() else seconds
                found.append((renderer["videoId"], text, seconds))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


def pick_trailer(results, title, year="", lang="vf"):
    """Prefer a trailer in the requested language whose title names the film."""
    def words(text):
        return set(re.findall(r"[a-z0-9]+", torrent_fold(text).lower())) - {"the", "le", "la", "les", "de", "a"}

    wanted = words(title)
    language = r"\bvf\b|version fran[cç]aise|\bfrench\b" if lang == "vf" else r"\bvost(?:fr)?\b|\bvo\b|official trailer"
    best, best_score = None, None
    for rank, (video_id, video_title, seconds) in enumerate(results):
        low = torrent_fold(video_title).lower()
        score = -0.1 * rank
        if re.search(language, low):
            score += 3
        if re.search(r"bande[ -]?annonce|trailer|teaser", low):
            score += 2
        score += min(len(words(video_title) & wanted), 4) * 0.5
        if year and year in video_title:
            score += 1
        elif year and any(abs(int(other) - int(year)) > 1 for other in re.findall(r"\b(?:19|20)\d{2}\b", video_title)):
            score -= 3  # another film of the same name (« Les Misérables » 2012 vs 2026)
        if seconds and (seconds < 20 or seconds > 360):
            score -= 5
        if best_score is None or score > best_score:
            best, best_score = video_id, score
    return best


def torrent_fold(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


def resolve_trailer_info(title="", year="", yt_id="", lang="vf"):
    """Recherche une bande-annonce VF/VO sur YouTube."""
    title = (title or "").strip()
    year = str(year or "").strip()[:4]
    yt_id = (yt_id or "").strip()
    lang = (lang or "vf").strip().lower()
    cache_key = f"trailer_v2:{title}:{year}:{yt_id}:{lang}"

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
                # The first id of the page may be an international or unrelated video.
                chosen_id = pick_trailer(_youtube_video_results(html), title, year, lang) or ""
                if not chosen_id:
                    ids = list(dict.fromkeys(re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)))
                    chosen_id = ids[0] if ids else ""
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


def series_videos(imdb_id):
    """A series' episodes (number, name, release day, still), light and cached 6 h,
    for « Nouveaux épisodes ». None when Cinemeta does not answer (not cached)."""
    if not re.fullmatch(r"tt\d{1,12}", str(imdb_id or "")):
        return None

    def fetch():
        try:
            data = http_json(f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json", timeout=6, retries=1)
        except Exception:
            return None
        videos = ((data or {}).get("meta") or {}).get("videos")
        if not isinstance(videos, list):
            return None
        return [{"season": v.get("season"), "episode": v.get("episode") or v.get("number"),
                 "name": html.unescape(str(v.get("name") or v.get("title") or "")),
                 "released": str(v.get("released") or "")[:10], "thumbnail": v.get("thumbnail") or ""}
                for v in videos if isinstance(v, dict)]

    return cached_get(f"series_videos_v1:{imdb_id}", 6 * 3600, fetch)


def get_series_meta(imdb_id):
    return get_media_meta(imdb_id, "series")


def _series_episode_list(imdb_id, allow_network=False):
    """Episode numbers of a series: RAM cache, disk cache, then (if allowed) a light Cinemeta fetch.

    The RAM cache is empty after every restart, so relying on it alone recorded
    "S01E09" as the next episode after a season finale.
    """
    for key in (f"meta_fr_v2:series:{imdb_id}", f"meta_fr:series:{imdb_id}", f"meta:series:{imdb_id}"):
        hit = MEM_CACHE.get(key)
        videos = ((hit or {}).get("val") or {}).get("videos")
        if videos:
            return videos
    stored = kino_db.db_cache_get(f"meta_fr_v2:series:{imdb_id}")
    if stored and stored.get("videos"):
        return stored["videos"]
    if not allow_network or not re.fullmatch(r"tt\d{1,12}", str(imdb_id)):
        return None

    def _fetch():
        # Short timeout, no retry: this runs while a history entry is being saved.
        data = http_json(f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json", timeout=4, retries=0)
        return [{"season": v.get("season"), "episode": v.get("episode") or v.get("number")}
                for v in ((data or {}).get("meta") or {}).get("videos") or []]

    return cached_get(f"series_episodes_v1:{imdb_id}", 86400, _fetch)


def absolute_episode_number(imdb_id, season, episode, allow_network=False):
    """Position of SxxEyy among all regular episodes (specials excluded), as anime groups
    number them: S02E01 of a show with 24 episodes in season 1 is « - 25 »."""
    try:
        videos = _series_episode_list(imdb_id, allow_network=allow_network) or []
        numbered = sorted({(int(v.get("season") or 0), int(v.get("episode") or v.get("number") or 0))
                           for v in videos} - {(0, 0)})
        numbered = [(se, ep) for se, ep in numbered if se > 0 and ep > 0]
        return numbered.index((int(season), int(episode))) + 1
    except (ValueError, TypeError):
        return None
    except Exception:
        return None


def next_series_episode(imdb_id, season, episode, allow_network=False):
    """(next_season, next_episode, finished). `finished` is True only when the episode
    list is known and nothing follows: the last episode of the series was watched."""
    s = int(season or 1)
    e = int(episode or 1)
    if not imdb_id:
        return s, e + 1, False
    try:
        videos = _series_episode_list(imdb_id, allow_network=allow_network) or []
        if not videos:
            return s, e + 1, False
        numbered = [(int(v.get("season") or 0), int(v.get("episode") or v.get("number") or 0)) for v in videos]
        same_season_next = [ep for se, ep in numbered if se == s and ep > e]
        if same_season_next:
            return s, min(same_season_next), False
        next_season_eps = [ep for se, ep in numbered if se == s + 1 and ep > 0]
        if next_season_eps:
            return s + 1, min(next_season_eps), False
        later = [(se, ep) for se, ep in numbered if se > s + 1 and ep > 0]
        if later:
            return min(later)[0], min(later)[1], False
        return s, e + 1, True
    except Exception:
        return s, e + 1, False


def _compute_next_series_episode(imdb_id, season, episode, allow_network=False):
    ns, ne, _finished = next_series_episode(imdb_id, season, episode, allow_network=allow_network)
    return ns, ne


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
    """Récupère les films (titre, année) depuis un pseudo ou une URL publique Letterboxd."""
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
        containers = re.findall(r'<li[^>]*class="[^"]*poster-container[^"]*"[^>]*>(.*?)</li>', body, re.DOTALL)
        for c in containers:
            title = ""
            year = ""
            user_rating = ""
            m_fn = re.search(r'data-film-name="([^"]+)"', c)
            if m_fn:
                title = html_unescape(m_fn.group(1).strip())
                m_yr = re.search(r'data-film-release-year="(\d{4})"', c)
                if m_yr:
                    year = m_yr.group(1).strip()
            if not title:
                m_in = re.search(r'data-item-name="([^"]+)"', c)
                if m_in:
                    title, year = _parse_title_year_str(html_unescape(m_in.group(1).strip()))
            if title:
                m_r = re.search(r'class="[^"]*rating\s+rated-(\d+)[^"]*"', c) or re.search(r'rated-(\d+)', c)
                if m_r:
                    try:
                        val = int(m_r.group(1)) / 2.0
                        user_rating = f"{val:.1f}".rstrip("0").rstrip(".")
                    except Exception:
                        pass
                elif re.search(r'data-rating="([0-9.]+)"', c):
                    try:
                        val = float(re.search(r'data-rating="([0-9.]+)"', c).group(1))
                        user_rating = f"{val:.1f}".rstrip("0").rstrip(".")
                    except Exception:
                        pass
                key = f"{title.lower()}|{year}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    entries.append({"title": title, "year": year, "user_rating": user_rating})
                    page_found += 1

        if page_found == 0:
            for raw_item in re.findall(r'data-item-name="([^"]+)"', body):
                title, year = _parse_title_year_str(raw_item)
                if title:
                    key = f"{title.lower()}|{year}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        entries.append({"title": title, "year": year, "user_rating": ""})
                        page_found += 1

        if page_found == 0:
            for m in re.finditer(r'data-film-name="([^"]+)"[^>]*?(?:data-film-release-year="(\d{4})")?', body):
                title = html_unescape(m.group(1).strip())
                year = (m.group(2) or "").strip()
                if title:
                    key = f"{title.lower()}|{year}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        entries.append({"title": title, "year": year, "user_rating": ""})
                        page_found += 1

        if page_found == 0 and "<letterboxd:filmTitle>" in body:
            for m in re.finditer(r"<letterboxd:filmTitle>([^<]+)</letterboxd:filmTitle>\s*(?:<letterboxd:filmYear>(\d{4})</letterboxd:filmYear>)?(?:\s*<letterboxd:memberRating>([0-9.]+)</letterboxd:memberRating>)?", body):
                title = html_unescape(m.group(1).strip())
                year = (m.group(2) or "").strip()
                ur_raw = (m.group(3) or "").strip()
                user_rating = ""
                if ur_raw:
                    try:
                        val = float(ur_raw)
                        user_rating = f"{val:.1f}".rstrip("0").rstrip(".")
                    except Exception:
                        pass
                if title:
                    key = f"{title.lower()}|{year}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        entries.append({"title": title, "year": year, "user_rating": user_rating})
                        page_found += 1

        if page_found == 0 or f"/page/{page + 1}/" not in body:
            break

    return entries


def parse_letterboxd_csv_or_text(csv_text):
    """Extrait une liste de {'title', 'year', 'imdb_id', 'user_rating'} depuis un fichier CSV Letterboxd / IMDb."""
    import csv
    import io
    text = (csv_text or "").lstrip("\ufeff").strip()
    if not text:
        return []

    entries = []
    seen = {}

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    name_idx = next((i for i, h in enumerate(header) if h in ("name", "title", "film", "movie", "original title")), -1)
    year_idx = next((i for i, h in enumerate(header) if h in ("year", "release year", "année")), -1)
    imdb_idx = next((i for i, h in enumerate(header) if h in ("const", "imdb", "imdb_id", "tconst")), -1)
    rating_idx = next((i for i, h in enumerate(header) if h in ("rating", "your rating", "user rating", "note", "user_rating", "my rating", "member rating")), -1)

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
            user_rating = ""
            if rating_idx != -1 and rating_idx < len(r):
                raw_rating = r[rating_idx].strip()
                if raw_rating:
                    try:
                        val = float(raw_rating.replace(",", "."))
                        if val > 0:
                            if val > 5.0:
                                val = round(val / 2.0, 1)
                            user_rating = f"{val:.1f}".rstrip("0").rstrip(".")
                    except Exception:
                        pass
            key = f"{imdb_id or title.lower()}|{year}"
            if key not in seen:
                entry = {"title": title, "year": year, "imdb_id": imdb_id, "user_rating": user_rating}
                seen[key] = entry
                entries.append(entry)
            elif user_rating and not seen[key].get("user_rating"):
                seen[key]["user_rating"] = user_rating
        return entries

    for r in rows:
        line = " ".join(c.strip() for c in r if c.strip())
        if not line or line.lower().startswith("date,name"):
            continue
        title, year = _parse_title_year_str(line)
        if title:
            key = f"{title.lower()}|{year}"
            if key not in seen:
                entry = {"title": title, "year": year, "imdb_id": "", "user_rating": ""}
                seen[key] = entry
                entries.append(entry)
    return entries


def _letterboxd_norm(text):
    """Title for comparison: no accents, no punctuation, single spaces."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def _result_year(item):
    year = str(item.get("releaseInfo") or item.get("year") or "")[:4]
    return int(year) if year.isdigit() else None


def _pick_letterboxd_match(results, title, year, imdb_id=""):
    """The catalogue result that is this Letterboxd film, or None.

    Never a guess: with a year, a result must be within three years of it
    (same title: release years differ between countries), within a year (title
    contained), or within a year (another title, for films Letterboxd names in
    their original language); without a
    year, the same title. Until 1.1.90 the first result was taken when nothing
    matched: « Lupin » (2021) became « The Castle of Cagliostro » (1979)."""
    if imdb_id.startswith("tt"):
        exact = next((m for m in results if m.get("id") == imdb_id), None)
        if exact:
            return exact
    wanted = _letterboxd_norm(title)
    target = int(year) if str(year).isdigit() else None

    def close(item, gap):
        found = _result_year(item)
        return target is not None and found is not None and abs(found - target) <= gap

    if target is None:
        return next((m for m in results if _letterboxd_norm(m.get("name")) == wanted), None)
    for test in (
        lambda m: _letterboxd_norm(m.get("name")) == wanted and close(m, 3),
        lambda m: wanted and (wanted in _letterboxd_norm(m.get("name")) or _letterboxd_norm(m.get("name")) in wanted) and close(m, 1),
        lambda m: close(m, 1),
    ):
        found = next((m for m in results if test(m)), None)
        if found:
            return found
    return None


def _old_letterboxd_pick(results, title, year, imdb_id=""):
    """What KINO 1.1.90 and before chose for this entry (films only), to find
    the wrong films it added: the same tests, then the first result."""
    chosen = _pick_letterboxd_match(results, title, year, imdb_id) if str(year).isdigit() or imdb_id else None
    if chosen:
        return chosen
    t_low = title.lower()
    return next((m for m in results if str(m.get("name") or "").strip().lower() == t_low), results[0] if results else None)


def _resolve_letterboxd_entries(raw_entries, max_items=150, wrong_ids=None):
    """Letterboxd entries → catalogue items (films, then series). Entries that
    match nothing are left out. wrong_ids (a set) receives the films the old
    matching chose instead, so an import can take them back."""
    from concurrent.futures import ThreadPoolExecutor
    batch = (raw_entries or [])[:max_items]
    if not batch:
        return []

    def _resolve_one(entry):
        title = (entry.get("title") or "").strip()
        target_yr = (entry.get("year") or "").strip()
        imdb_id = (entry.get("imdb_id") or "").strip()
        user_rating = (entry.get("user_rating") or "").strip()
        if not title and not imdb_id:
            return None
        try:
            movies = search_cinemeta(title or imdb_id, "movie") or []
            chosen, kind = _pick_letterboxd_match(movies, title, target_yr, imdb_id), "movie"
            if not chosen:
                series = search_cinemeta(title or imdb_id, "series") or []
                chosen, kind = _pick_letterboxd_match(series, title, target_yr, imdb_id), "series"
            old = _old_letterboxd_pick(movies, title, target_yr, imdb_id) if movies else None
            old_year = _result_year(old) if old else None
            # Only a clear mistake is taken back: years far apart, as « Lupin » (2021) → 1979.
            if (old and old.get("id") and (not chosen or old.get("id") != chosen.get("id"))
                    and target_yr.isdigit() and old_year is not None and abs(old_year - int(target_yr)) > 3):
                entry["_old_id"] = old["id"]
            if not chosen or not chosen.get("id"):
                return None
            cid = chosen.get("id")
            rating_str = str(chosen.get("imdbRating") or "").strip()
            if not rating_str and cid.startswith("tt"):
                try:
                    mdata = cached_get(
                        f"cinemeta_lite:{kind}:{cid}",
                        86400,
                        lambda: http_json(f"https://v3-cinemeta.strem.io/meta/{kind}/{cid}.json", timeout=4),
                    )
                    m_inner = (mdata or {}).get("meta") or {}
                    rating_str = str(m_inner.get("imdbRating") or "").strip()
                except Exception:
                    pass
            return {
                "id": cid,
                "name": chosen.get("name") or title,
                "type": kind,
                "year": str(chosen.get("releaseInfo") or chosen.get("year") or target_yr)[:4],
                "poster": chosen.get("poster") or f"https://images.metahub.space/poster/medium/{cid}/img",
                "imdbRating": rating_str,
                "user_rating": user_rating,
                "letterboxd": {"title": title, "year": target_yr},
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        resolved = [r for r in pool.map(_resolve_one, batch) if r]
    if wrong_ids is not None:
        good = {r["id"] for r in resolved}
        wrong_ids.update(e["_old_id"] for e in batch if e.get("_old_id") and e["_old_id"] not in good)
    return resolved


def import_letterboxd_watchlist(payload):
    """Importe une Watchlist et/ou les Films déjà vus depuis Letterboxd avec conservation des notes."""
    url_or_user = (payload.get("url_or_user") or "").strip()
    csv_text = (payload.get("csv_text") or "").strip()
    csv_filename = (payload.get("csv_filename") or "").lower()
    mode = (payload.get("mode") or "both").strip().lower()
    # The user asked to bring back films they had removed in KINO.
    restore_removed = payload.get("restore_removed") is True
    if mode not in ("watchlist", "watched", "both"):
        mode = "both"

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
    wrong_watched = set()
    resolved_watched = _resolve_letterboxd_entries(watched_raw, max_items=180, wrong_ids=wrong_watched) if watched_raw else []

    now_ts = int(time.time())
    watched_entries = [
        {
            "id": item["id"],
            "name": item.get("name", ""),
            "type": item.get("type") or "movie",
            "year": item.get("year", ""),
            "poster": item.get("poster", ""),
            "imdbRating": item.get("imdbRating", ""),
            "user_rating": item.get("user_rating") or "",
            "position": 7200,
            "duration": 7200,
            "progress_pct": 100.0,
            "completed": True,
            "imported_watched": True,
            # A series seen on Letterboxd: seen to the end (« Revoir »), never « À continuer ».
            **({"series_finished": True} if item.get("type") == "series" else {}),
            "letterboxd": item.get("letterboxd") or {},
            "updated_at": now_ts,
        }
        for item in resolved_watched
    ]
    # Films a previous import chose wrongly for these entries, never played: taken back.
    corrected = kino_db.db_drop_wrong_imports(wrong_watched)
    # Écrit directement en base, sans passer par save_config : seuls les films
    # absents sont ajoutés, Ma Liste et l'historique existants restent intacts.
    added = kino_db.db_import_user_lists(resolved_wl, watched_entries, restore_removed=restore_removed)

    if url_or_user and not url_or_user.startswith("http") and "/" not in url_or_user:
        save_config({"letterboxd_user": url_or_user.strip().lstrip("@")})
    return {
        "watchlist": kino_db.db_get_watchlist(),
        "history": kino_db.db_get_history(),
        "found_count": len(wl_raw) + len(watched_raw),
        "added_count": len(added["watchlist"]),
        "found_wl_count": len(wl_raw),
        "added_wl_count": len(added["watchlist"]),
        "found_watched_count": len(watched_raw),
        "added_watched_count": len(added["history"]),
        # Found on Letterboxd but removed in KINO earlier: left out unless asked.
        "removed_wl_count": len(added["removed"]["watchlist"]),
        "removed_watched_count": len(added["removed"]["history"]),
        "corrected_watched_count": len(corrected),
        "unmatched_count": (len(wl_raw) - len(resolved_wl)) + (len(watched_raw) - len(resolved_watched)),
    }


def import_letterboxd_custom_list(list_url):
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
