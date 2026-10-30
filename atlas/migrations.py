"""Schema migrations. Append new entries; never edit released ones.

Two databases are used:
  main  - library metadata, tags, annotations, notes (irreplaceable user data)
  idx   - extracted text segments + FTS5 index (can always be rebuilt)
"""

MAIN_MIGRATIONS: list[str] = [
# ---- 1 -------------------------------------------------------------------
"""
CREATE TABLE library_roots (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    recursive INTEGER NOT NULL DEFAULT 1,
    volume_serial TEXT NOT NULL DEFAULT '',
    volume_label TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'local',
    online INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT,
    last_scan TEXT,
    added TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    root_id INTEGER NOT NULL REFERENCES library_roots(id),
    rel_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    format TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL DEFAULT '',
    available INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    year INTEGER,
    isbn TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    series_index REAL,
    description TEXT NOT NULL DEFAULT '',
    page_count INTEGER,
    reading_status TEXT NOT NULL DEFAULT 'unread',
    rating INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    personal_notes TEXT NOT NULL DEFAULT '',
    author_sort TEXT NOT NULL DEFAULT '',
    metadata_edited INTEGER NOT NULL DEFAULT 0,
    index_status TEXT NOT NULL DEFAULT 'pending',
    index_detail TEXT NOT NULL DEFAULT '',
    low_text_pages INTEGER NOT NULL DEFAULT 0,
    cover_path TEXT NOT NULL DEFAULT '',
    toc_json TEXT NOT NULL DEFAULT '',
    import_date TEXT NOT NULL DEFAULT (datetime('now')),
    last_indexed TEXT,
    last_read TEXT,
    UNIQUE(root_id, rel_path)
);
CREATE INDEX idx_books_fp ON books(fingerprint);
CREATE INDEX idx_books_status ON books(index_status);
CREATE INDEX idx_books_title ON books(title COLLATE NOCASE);
CREATE INDEX idx_books_added ON books(import_date);
CREATE INDEX idx_books_lastread ON books(last_read);

CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE book_authors (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id)
);
CREATE INDEX idx_ba_author ON book_authors(author_id);

CREATE TABLE book_terms (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('genre','subject')),
    value TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (book_id, kind, value)
);
CREATE INDEX idx_terms_value ON book_terms(kind, value);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    parent_id INTEGER REFERENCES tags(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX idx_tags_unique ON tags(COALESCE(parent_id, 0), name);
CREATE TABLE book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, tag_id)
);
CREATE INDEX idx_bt_tag ON book_tags(tag_id);

CREATE TABLE collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE collection_books (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    PRIMARY KEY (collection_id, book_id)
);
CREATE INDEX idx_cb_book ON collection_books(book_id);

CREATE TABLE autotag_rules (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('directory','filename','metadata','keyword')),
    pattern TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    auto_apply INTEGER NOT NULL DEFAULT 0,
    min_keyword_hits INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE tag_suggestions (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    rule_id INTEGER REFERENCES autotag_rules(id) ON DELETE SET NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (book_id, tag_id)
);

CREATE TABLE reading_progress (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    locator TEXT NOT NULL DEFAULT '{}',
    percent REAL NOT NULL DEFAULT 0,
    zoom REAL,
    updated TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE reading_sessions (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    started TEXT NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_rs_book ON reading_sessions(book_id);

CREATE TABLE annotations (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('bookmark','highlight','note')),
    selected_text TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '{}',
    loc_num INTEGER,
    loc_label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#f2c94c',
    invalid INTEGER NOT NULL DEFAULT 0,
    book_fingerprint TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT (datetime('now')),
    modified TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_ann_book ON annotations(book_id);

CREATE TABLE clippings (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
    book_uuid TEXT NOT NULL DEFAULT '',
    book_title TEXT NOT NULL DEFAULT '',
    book_authors TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    loc_type TEXT NOT NULL DEFAULT '',
    loc_num INTEGER,
    loc_label TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '{}',
    text TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE clipping_tags (
    clipping_id INTEGER NOT NULL REFERENCES clippings(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (clipping_id, tag_id)
);
CREATE TABLE clipping_links (
    a INTEGER NOT NULL REFERENCES clippings(id) ON DELETE CASCADE,
    b INTEGER NOT NULL REFERENCES clippings(id) ON DELETE CASCADE,
    PRIMARY KEY (a, b)
);

CREATE TABLE topics (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL UNIQUE COLLATE NOCASE,
    body TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT (datetime('now')),
    modified TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE topic_tags (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (topic_id, tag_id)
);
CREATE TABLE topic_books (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    PRIMARY KEY (topic_id, book_id)
);
CREATE TABLE topic_clippings (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    clipping_id INTEGER NOT NULL REFERENCES clippings(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (topic_id, clipping_id)
);
CREATE TABLE topic_links (
    from_topic INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    to_topic INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    PRIMARY KEY (from_topic, to_topic)
);

CREATE TABLE saved_searches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    query TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '{}',
    result_count INTEGER NOT NULL DEFAULT 0,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE scan_log (
    id INTEGER PRIMARY KEY,
    started TEXT NOT NULL,
    finished TEXT,
    root_id INTEGER,
    discovered INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
""",
]

IDX_MIGRATIONS: list[str] = [
"""
CREATE TABLE idx.segments (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    loc_type TEXT NOT NULL,
    loc_num INTEGER NOT NULL DEFAULT 0,
    loc_label TEXT NOT NULL DEFAULT '',
    char_start INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL
);
CREATE INDEX idx.idx_seg_book ON segments(book_id, source, seq);
CREATE VIRTUAL TABLE idx.segments_fts USING fts5(
    text, content='segments', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2', prefix='2 3'
);
CREATE TRIGGER idx.segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER idx.segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER idx.segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
""",
# ---- 2: narrow location table so search joins do not touch the wide text rows
"""
CREATE TABLE idx.seg_meta (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL,
    loc_type TEXT NOT NULL,
    loc_num INTEGER NOT NULL,
    loc_label TEXT NOT NULL,
    char_start INTEGER NOT NULL
);
INSERT INTO idx.seg_meta SELECT id, book_id, loc_type, loc_num, loc_label, char_start FROM segments;
CREATE TRIGGER idx.segmeta_ai AFTER INSERT ON segments BEGIN
    INSERT INTO seg_meta(id, book_id, loc_type, loc_num, loc_label, char_start)
    VALUES (new.id, new.book_id, new.loc_type, new.loc_num, new.loc_label, new.char_start);
END;
CREATE TRIGGER idx.segmeta_ad AFTER DELETE ON segments BEGIN
    DELETE FROM seg_meta WHERE id = old.id;
END;
""",
]
