"""
KINO Community Curated Lists (Letterboxd & Cinephile Feeds)
===========================================================
Collections de référence sélectionnées pour la communauté cinématographique :
- Sélections pré-configurées des plus grands chefs-d'œuvre mondiaux
- Importateur direct de listes Letterboxd par simple URL ou lien court boxd.it
"""

COMMUNITY_CURATED_LISTS = [
    {
        "id": "letterboxd_top250",
        "title": "🏆 Letterboxd Official Top 250",
        "badge": "TOP 250",
        "curator": "Communauté Letterboxd",
        "description": "Le classement mondial ultime des 250 meilleurs films de fiction de l'histoire du cinéma selon les notes de millions de cinéphiles.",
        "icon": "🏆",
        "url": "https://letterboxd.com/dave/list/official-top-250-narrative-feature-films/",
        "sample_titles": [
            "Parasite", "The Godfather", "12 Angry Men", "Spirited Away", "Seven Samurai",
            "Pulp Fiction", "City of God", "Oldboy", "GoodFellas", "The Dark Knight",
            "Cinema Paradiso", "Fight Club", "Interstellar", "Schindler's List", "Inception"
        ]
    },
    {
        "id": "scifi_masterpieces",
        "title": "🌌 Chef-d'œuvres de la Science-Fiction",
        "badge": "SCI-FI",
        "curator": "KINO Cinephile Hub",
        "description": "Voyages temporels, dystopies cyberpunk et odyssées spatiales qui ont redéfini notre vision de l'univers et du futur.",
        "icon": "🌌",
        "url": "https://letterboxd.com/official/list/sci-fi-hall-of-fame/",
        "sample_titles": [
            "Blade Runner 2049", "Interstellar", "2001: A Space Odyssey", "Arrival",
            "The Matrix", "Children of Men", "Alien", "Ex Machina", "Dune", "Akira",
            "Solaris", "Stalker", "Terminator 2", "Her", "Twelve Monkeys"
        ]
    },
    {
        "id": "korean_thrillers",
        "title": "🔪 Thrillers Coréens & Néo-Noir",
        "badge": "NOIR",
        "curator": "K-Cinema Club",
        "description": "Tensions viscérales, vengeances implacables et polars sombres : la quintessence du thriller sud-coréen moderne.",
        "icon": "🔪",
        "url": "https://letterboxd.com/matthewbarn/list/korean-cinema-top-100/",
        "sample_titles": [
            "Memories of Murder", "Oldboy", "I Saw the Devil", "The Chaser",
            "The Handmaiden", "A Bittersweet Life", "Decision to Leave", "Mother",
            "New World", "The Man from Nowhere", "Parasite", "Burning"
        ]
    },
    {
        "id": "japanese_animation",
        "title": "🎌 Joyaux de l'Animation Japonaise",
        "badge": "ANIME",
        "curator": "Tokyo Cinema Archives",
        "description": "Des merveilles poétiques de Hayao Miyazaki aux labyrinthes psychologiques de Satoshi Kon et l'élégance de Makoto Shinkai.",
        "icon": "🎌",
        "url": "https://letterboxd.com/official/list/essential-anime/",
        "sample_titles": [
            "Spirited Away", "Princess Mononoke", "Perfect Blue", "Your Name",
            "Grave of the Fireflies", "Paprika", "Ghost in the Shell", "Akira",
            "A Silent Voice", "Millennium Actress", "Howl's Moving Castle", "Tokyo Godfathers"
        ]
    },
    {
        "id": "criterion_essentials",
        "title": "📼 Sélection Prestige Criterion Collection",
        "badge": "CRITERION",
        "curator": "The Criterion Channel",
        "description": "Les restaurations d'orfèvre et les géants du 7ème art : Kurosawa, Bergman, Fellini, Kubrick, Varda, Wong Kar-wai.",
        "icon": "📼",
        "url": "https://letterboxd.com/official/list/the-criterion-collection/",
        "sample_titles": [
            "In the Mood for Love", "Seven Samurai", "The Seventh Seal", "Persona",
            "8½", "Stalker", "Cleo from 5 to 7", "Harakiri", "Bicycle Thieves",
            "Rashomon", "Breathless", "Yi Yi", "Mulholland Drive"
        ]
    },
    {
        "id": "mindfuck_thrillers",
        "title": "🧠 Mindfuck & Mystères Psychologiques",
        "badge": "MINDFUCK",
        "curator": "Twisted Cinema",
        "description": "Films à twist, réalités distordues et énigmes déroutantes qui vous laisseront sans voix jusqu'au générique de fin.",
        "icon": "🧠",
        "url": "https://letterboxd.com/official/list/mindfuck-movies/",
        "sample_titles": [
            "Shutter Island", "Memento", "The Prestige", "Mulholland Drive",
            "Fight Club", "Enemy", "Coherence", "Primal Fear", "The Usual Suspects",
            "Donnie Darko", "The Sixth Sense", "Predestination", "Triangle"
        ]
    }
]


def get_curated_lists():
    return list(COMMUNITY_CURATED_LISTS)

get_curated_collections = get_curated_lists
