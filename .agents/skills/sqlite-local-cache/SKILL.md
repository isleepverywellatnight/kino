---
name: sqlite-local-cache
description: Patterns for robust, zero-dependency local data storage, caching, and full-text search (FTS5) using Python's standard sqlite3 module for desktop media centers and streaming applications.
---

# SQLite Local Cache & Fast Search (Python Standard Library)

Guide pratique pour stocker, indexer et rechercher localement des catalogues de films, séries, historiques de visionnage et métadonnées sans dépendance externe.

---

## ⚡ 1. Configuration Haute Performance

Pour une réactivité maximale dans une interface desktop sans bloquer le thread principal :
```python
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".kino_data.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row  # Accès aux colonnes par nom
    
    # Pragmas essentiels pour la réactivité desktop
    conn.execute("PRAGMA journal_mode = WAL;")        # Write-Ahead Logging (lectures concurrentes sans verrou)
    conn.execute("PRAGMA synchronous = NORMAL;")       # Équilibre parfait vitesse / intégrité
    conn.execute("PRAGMA cache_size = -64000;")        # 64 Mo de cache en mémoire vive
    conn.execute("PRAGMA temp_store = MEMORY;")        # Tables temporaires en RAM
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
```

---

## 🔍 2. Recherche Textuelle Instantanée avec FTS5

Pour rechercher parmi 100 000 titres instantanément avec tolérance orthographique :

```sql
-- Table virtuelle de recherche plein texte
CREATE VIRTUAL TABLE IF NOT EXISTS media_search USING fts5(
    imdb_id,
    title,
    original_title,
    cast_members,
    genres,
    content='media',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Requête de recherche avec préfixe (autocomplétion en temps réel)
SELECT m.* FROM media m
JOIN media_search s ON m.id = s.rowid
WHERE media_search MATCH ?
ORDER BY rank
LIMIT 30;
```

---

## 📋 3. Schéma type pour Media Center

```sql
-- Table principale des médias en cache
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imdb_id TEXT UNIQUE,
    tmdb_id INTEGER,
    media_type TEXT CHECK(media_type IN ('movie', 'series')),
    title TEXT NOT NULL,
    year INTEGER,
    poster_url TEXT,
    backdrop_url TEXT,
    overview TEXT,
    rating REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Suivi de progression (Reprendre la lecture)
CREATE TABLE IF NOT EXISTS watch_history (
    imdb_id TEXT NOT NULL,
    season INTEGER DEFAULT 0,
    episode INTEGER DEFAULT 0,
    progress_seconds REAL NOT NULL,
    total_seconds REAL NOT NULL,
    completed BOOLEAN DEFAULT 0,
    last_watched TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (imdb_id, season, episode)
);

-- Index pour requêtes éclairs
CREATE INDEX IF NOT EXISTS idx_media_type_year ON media(media_type, year DESC);
CREATE INDEX IF NOT EXISTS idx_history_last_watched ON watch_history(last_watched DESC);
```

---

## 🛡️ 4. Bonnes Pratiques & Concurrence
1. **Opérations par lots (`executemany`)** : Lors de l'import de Watchlists Letterboxd ou de catalogues TMDB volumineux, toujours utiliser des transactions (`with conn:` ou `BEGIN ... COMMIT`) pour insérer 10 000 lignes en moins de 100 ms.
2. **Migration douce** : Stocker un numéro de version de schéma (`PRAGMA user_version;`) pour appliquer automatiquement les évolutions de base de données sans corrompre les données utilisateur.
