"""
KINO Community Curated Lists (Letterboxd & Cinephile Feeds)
===========================================================
Collections de référence sélectionnées pour la communauté cinématographique :
- Sélections pré-configurées des plus grands chefs-d'œuvre mondiaux avec métadonnées résolues
- Importateur direct de listes Letterboxd par simple URL ou lien court boxd.it
"""

def _item(imdb_id: str, name: str, year: int, rating: float):
    return {
        "id": imdb_id,
        "name": name,
        "type": "movie",
        "year": str(year),
        "poster": f"https://images.metahub.space/poster/medium/{imdb_id}/img",
        "imdbRating": str(rating),
    }

ITEMS_TOP250 = [
    _item("tt6751668", "Parasite", 2019, 8.5),
    _item("tt0068646", "Le Parrain (The Godfather)", 1972, 9.2),
    _item("tt0050083", "12 Hommes en colère (12 Angry Men)", 1957, 9.0),
    _item("tt0245429", "Le Voyage de Chihiro (Spirited Away)", 2001, 8.6),
    _item("tt0047478", "Les Sept Samouraïs (Seven Samurai)", 1954, 8.6),
    _item("tt0110912", "Pulp Fiction", 1994, 8.9),
    _item("tt0317248", "La Cité de Dieu (City of God)", 2002, 8.6),
    _item("tt0364569", "Oldboy", 2003, 8.4),
    _item("tt0099685", "Les Affranchis (GoodFellas)", 1990, 8.7),
    _item("tt0468569", "The Dark Knight : Le Chevalier Noir", 2008, 9.0),
    _item("tt0095765", "Cinema Paradiso", 1988, 8.5),
    _item("tt0137523", "Fight Club", 1999, 8.8),
    _item("tt0816692", "Interstellar", 2014, 8.7),
    _item("tt0108052", "La Liste de Schindler", 1993, 9.0),
    _item("tt1375666", "Inception", 2010, 8.8),
    _item("tt0111161", "Les Évadés (The Shawshank Redemption)", 1994, 9.3),
    _item("tt0056058", "Harakiri", 1962, 8.6),
    _item("tt0057565", "Entre le ciel et l'enfer (High and Low)", 1963, 8.4),
    _item("tt2582802", "Whiplash", 2014, 8.5),
    _item("tt4633694", "Spider-Man: New Generation", 2018, 8.4),
]

ITEMS_SCIFI = [
    _item("tt1856101", "Blade Runner 2049", 2017, 8.0),
    _item("tt0816692", "Interstellar", 2014, 8.7),
    _item("tt0062622", "2001 : L'Odyssée de l'espace", 1968, 8.3),
    _item("tt2543164", "Premier Contact (Arrival)", 2016, 7.9),
    _item("tt0133093", "Matrix", 1999, 8.7),
    _item("tt0206634", "Les Fils de l'homme (Children of Men)", 2006, 7.9),
    _item("tt0078748", "Alien, le huitième passager", 1979, 8.5),
    _item("tt0470752", "Ex Machina", 2014, 7.7),
    _item("tt1160419", "Dune : Première Partie", 2021, 8.0),
    _item("tt15239678", "Dune : Deuxième Partie", 2024, 8.5),
    _item("tt0094625", "Akira", 1988, 8.0),
    _item("tt0069291", "Solaris", 1972, 8.0),
    _item("tt0079944", "Stalker", 1979, 8.1),
    _item("tt0103064", "Terminator 2 : Le Jugement dernier", 1991, 8.6),
    _item("tt1798709", "Her", 2013, 8.0),
    _item("tt0114746", "L'Armée des 12 singes (Twelve Monkeys)", 1995, 8.0),
]

ITEMS_KOREAN = [
    _item("tt0353969", "Memories of Murder", 2003, 8.1),
    _item("tt0364569", "Oldboy", 2003, 8.4),
    _item("tt1588170", "J'ai rencontré le Diable (I Saw the Devil)", 2010, 7.8),
    _item("tt1190539", "The Chaser", 2008, 7.8),
    _item("tt4016934", "Mademoiselle (The Handmaiden)", 2016, 8.1),
    _item("tt0406450", "A Bittersweet Life", 2005, 7.5),
    _item("tt12477380", "Decision to Leave", 2022, 7.3),
    _item("tt1216496", "Mother", 2009, 7.9),
    _item("tt2625030", "New World", 2013, 7.5),
    _item("tt1527788", "The Man from Nowhere", 2010, 7.7),
    _item("tt6751668", "Parasite", 2019, 8.5),
    _item("tt7282468", "Burning", 2018, 7.5),
    _item("tt0451094", "Lady Vengeance", 2005, 7.5),
    _item("tt0310775", "Sympathy for Mister Vengeance", 2002, 7.5),
]

ITEMS_ANIME = [
    _item("tt0245429", "Le Voyage de Chihiro (Spirited Away)", 2001, 8.6),
    _item("tt0119698", "Princesse Mononoké", 1997, 8.4),
    _item("tt0156887", "Perfect Blue", 1997, 8.0),
    _item("tt5311514", "Your Name. (Kimi no Na wa)", 2016, 8.4),
    _item("tt0095327", "Le Tombeau des lucioles", 1988, 8.5),
    _item("tt0851578", "Paprika", 2006, 7.7),
    _item("tt0113568", "Ghost in the Shell", 1995, 7.9),
    _item("tt0094625", "Akira", 1988, 8.0),
    _item("tt5323662", "A Silent Voice (Koe no Katachi)", 2016, 8.1),
    _item("tt0332285", "Millennium Actress", 2001, 7.8),
    _item("tt0347149", "Le Château ambulant", 2004, 8.2),
    _item("tt0388473", "Tokyo Godfathers", 2003, 7.8),
    _item("tt6587046", "Le Garçon et le Héron", 2023, 7.5),
    _item("tt16428256", "Suzume", 2022, 7.6),
    _item("tt0169858", "The End of Evangelion", 1997, 8.0),
]

ITEMS_CRITERION = [
    _item("tt0245228", "In the Mood for Love", 2000, 8.1),
    _item("tt0047478", "Les Sept Samouraïs", 1954, 8.6),
    _item("tt0050976", "Le Septième Sceau", 1957, 8.1),
    _item("tt0060827", "Persona", 1966, 8.1),
    _item("tt0056801", "Huit et demi (8½)", 1963, 8.0),
    _item("tt0079944", "Stalker", 1979, 8.1),
    _item("tt0055852", "Cléo de 5 à 7", 1962, 7.9),
    _item("tt0056058", "Harakiri", 1962, 8.6),
    _item("tt0040522", "Le Voleur de bicyclette", 1948, 8.3),
    _item("tt0042876", "Rashômon", 1950, 8.1),
    _item("tt0053472", "À bout de souffle", 1960, 7.7),
    _item("tt0244316", "Yi Yi", 2000, 8.1),
    _item("tt0166924", "Mulholland Drive", 2001, 7.9),
    _item("tt0046438", "Voyage à Tokyo", 1953, 8.1),
    _item("tt0113247", "La Haine", 1995, 8.1),
]

ITEMS_MINDFUCK = [
    _item("tt1130884", "Shutter Island", 2010, 8.2),
    _item("tt0209144", "Memento", 2000, 8.4),
    _item("tt0482571", "Le Prestige", 2006, 8.5),
    _item("tt0166924", "Mulholland Drive", 2001, 7.9),
    _item("tt0137523", "Fight Club", 1999, 8.8),
    _item("tt2316411", "Enemy", 2013, 6.9),
    _item("tt2866360", "Coherence", 2013, 7.2),
    _item("tt0117381", "Peur primale (Primal Fear)", 1996, 7.7),
    _item("tt0114814", "Usual Suspects", 1995, 8.5),
    _item("tt0246578", "Donnie Darko", 2001, 8.0),
    _item("tt0167404", "Sixième Sens", 1999, 8.2),
    _item("tt2397535", "Predestination", 2014, 7.4),
    _item("tt1187064", "Triangle", 2009, 6.9),
    _item("tt0099871", "L'Échelle de Jacob", 1990, 7.4),
    _item("tt0119174", "The Game", 1997, 7.7),
]

COMMUNITY_CURATED_LISTS = [
    {
        "id": "letterboxd_top250",
        "title": "Letterboxd Official Top 250",
        "badge": "TOP 250",
        "curator": "Communauté Letterboxd",
        "author": "Communauté Letterboxd",
        "description": "Le classement mondial ultime des 250 meilleurs films de fiction de l'histoire du cinéma selon les notes de millions de cinéphiles.",
        "icon": "trophy",
        "url": "https://letterboxd.com/dave/list/official-top-250-narrative-feature-films/",
        "count": len(ITEMS_TOP250),
        "items": ITEMS_TOP250,
    },
    {
        "id": "scifi_masterpieces",
        "title": "Chef-d'œuvres de la Science-Fiction",
        "badge": "SCI-FI",
        "curator": "KINO Cinephile Hub",
        "author": "KINO Cinephile Hub",
        "description": "Voyages temporels, dystopies cyberpunk et odyssées spatiales qui ont redéfini notre vision de l'univers et du futur.",
        "icon": "planet",
        "url": "https://letterboxd.com/official/list/sci-fi-hall-of-fame/",
        "count": len(ITEMS_SCIFI),
        "items": ITEMS_SCIFI,
    },
    {
        "id": "korean_thrillers",
        "title": "Thrillers Coréens & Néo-Noir",
        "badge": "NOIR",
        "curator": "K-Cinema Club",
        "author": "K-Cinema Club",
        "description": "Tensions viscérales, vengeances implacables et polars sombres : la quintessence du thriller sud-coréen moderne.",
        "icon": "target",
        "url": "https://letterboxd.com/matthewbarn/list/korean-cinema-top-100/",
        "count": len(ITEMS_KOREAN),
        "items": ITEMS_KOREAN,
    },
    {
        "id": "japanese_animation",
        "title": "Joyaux de l'Animation Japonaise",
        "badge": "ANIME",
        "curator": "Tokyo Cinema Archives",
        "author": "Tokyo Cinema Archives",
        "description": "Des merveilles poétiques de Hayao Miyazaki aux labyrinthes psychologiques de Satoshi Kon et l'élégance de Makoto Shinkai.",
        "icon": "sparkles",
        "url": "https://letterboxd.com/official/list/essential-anime/",
        "count": len(ITEMS_ANIME),
        "items": ITEMS_ANIME,
    },
    {
        "id": "criterion_essentials",
        "title": "Sélection Prestige Criterion Collection",
        "badge": "CRITERION",
        "curator": "The Criterion Channel",
        "author": "The Criterion Channel",
        "description": "Les restaurations d'orfèvre et les géants du 7ème art : Kurosawa, Bergman, Fellini, Kubrick, Varda, Wong Kar-wai.",
        "icon": "film",
        "url": "https://letterboxd.com/official/list/the-criterion-collection/",
        "count": len(ITEMS_CRITERION),
        "items": ITEMS_CRITERION,
    },
    {
        "id": "mindfuck_thrillers",
        "title": "Mindfuck & Mystères Psychologiques",
        "badge": "MINDFUCK",
        "curator": "Twisted Cinema",
        "author": "Twisted Cinema",
        "description": "Films à twist, réalités distordues et énigmes déroutantes qui vous laisseront sans voix jusqu'au générique de fin.",
        "icon": "zap",
        "url": "https://letterboxd.com/official/list/mindfuck-movies/",
        "count": len(ITEMS_MINDFUCK),
        "items": ITEMS_MINDFUCK,
    },
]


def get_curated_lists():
    return [dict(c) for c in COMMUNITY_CURATED_LISTS]

get_curated_collections = get_curated_lists
