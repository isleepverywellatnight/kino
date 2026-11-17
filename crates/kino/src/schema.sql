CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value_json TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );

CREATE TABLE IF NOT EXISTS persistent_cache (
                        cache_key TEXT PRIMARY KEY,
                        value_json TEXT,
                        expires_at REAL
                    );

CREATE INDEX IF NOT EXISTS idx_cache_expires ON persistent_cache(expires_at);

CREATE TABLE IF NOT EXISTS watchlist (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        type TEXT,
                        year TEXT,
                        poster TEXT,
                        imdb_rating TEXT,
                        added_at REAL,
                        user_rating TEXT
                    );

CREATE INDEX IF NOT EXISTS idx_watchlist_added ON watchlist(added_at DESC);

CREATE TABLE IF NOT EXISTS watch_history (
                        id TEXT PRIMARY KEY,
                        data_json TEXT,
                        last_watched REAL
                    );

CREATE INDEX IF NOT EXISTS idx_history_last_watched ON watch_history(last_watched DESC);

CREATE TABLE IF NOT EXISTS offline_downloads (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        status TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        title TEXT NOT NULL,
                        media_json TEXT NOT NULL,
                        subtitles_json TEXT NOT NULL DEFAULT '{}',
                        poster_url TEXT NOT NULL DEFAULT '',
                        root_path TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        part_path TEXT NOT NULL,
                        poster_path TEXT NOT NULL,
                        bytes_done INTEGER NOT NULL DEFAULT 0,
                        bytes_total INTEGER NOT NULL DEFAULT 0,
                        etag TEXT NOT NULL DEFAULT '',
                        last_modified TEXT NOT NULL DEFAULT '',
                        accept_ranges INTEGER NOT NULL DEFAULT 0,
                        error_code TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );

CREATE INDEX IF NOT EXISTS idx_offline_downloads_fingerprint ON offline_downloads(fingerprint);

CREATE INDEX IF NOT EXISTS idx_offline_downloads_created ON offline_downloads(created_at DESC);

CREATE TABLE IF NOT EXISTS tonight_feedback (
                        media_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        media_type TEXT NOT NULL DEFAULT 'movie',
                        genres_json TEXT NOT NULL DEFAULT '[]',
                        decision TEXT NOT NULL DEFAULT '',
                        until_ts REAL NOT NULL DEFAULT 0,
                        shown_count INTEGER NOT NULL DEFAULT 0,
                        last_shown REAL NOT NULL DEFAULT 0,
                        last_action_id TEXT NOT NULL DEFAULT '',
                        undo_json TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL DEFAULT 0
                    );

CREATE TABLE IF NOT EXISTS media_catalog (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        imdb_id TEXT UNIQUE,
                        title TEXT,
                        media_type TEXT,
                        year TEXT,
                        poster TEXT,
                        rating TEXT,
                        genres TEXT,
                        overview TEXT,
                        updated_at REAL
                    );

CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
                        title,
                        overview,
                        genres,
                        content='media_catalog',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    );

CREATE TRIGGER IF NOT EXISTS trg_media_insert AFTER INSERT ON media_catalog BEGIN
                        INSERT INTO media_fts(rowid, title, overview, genres)
                        VALUES (new.id, new.title, new.overview, new.genres);
                    END;

CREATE TRIGGER IF NOT EXISTS trg_media_update AFTER UPDATE ON media_catalog BEGIN
                        INSERT INTO media_fts(media_fts, rowid, title, overview, genres)
                        VALUES('delete', old.id, old.title, old.overview, old.genres);
                        INSERT INTO media_fts(rowid, title, overview, genres)
                        VALUES (new.id, new.title, new.overview, new.genres);
                    END;

CREATE TRIGGER IF NOT EXISTS trg_media_delete AFTER DELETE ON media_catalog BEGIN
                        INSERT INTO media_fts(media_fts, rowid, title, overview, genres)
                        VALUES('delete', old.id, old.title, old.overview, old.genres);
                    END;

CREATE TABLE IF NOT EXISTS media_aliases (
                        imdb_id TEXT PRIMARY KEY,
                        alias TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        year TEXT NOT NULL DEFAULT '',
                        media_type TEXT NOT NULL DEFAULT 'movie',
                        updated_at REAL
                    );

CREATE VIRTUAL TABLE IF NOT EXISTS media_alias_fts USING fts5(
                        alias, content='media_aliases', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    );

CREATE TRIGGER IF NOT EXISTS trg_alias_insert AFTER INSERT ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(rowid, alias) VALUES (new.rowid, new.alias);
                    END;

CREATE TRIGGER IF NOT EXISTS trg_alias_update AFTER UPDATE ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(media_alias_fts, rowid, alias) VALUES('delete', old.rowid, old.alias);
                        INSERT INTO media_alias_fts(rowid, alias) VALUES (new.rowid, new.alias);
                    END;

CREATE TRIGGER IF NOT EXISTS trg_alias_delete AFTER DELETE ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(media_alias_fts, rowid, alias) VALUES('delete', old.rowid, old.alias);
                    END;
