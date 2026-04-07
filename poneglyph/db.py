"""SQLite database setup with schema, FTS5, and connection management."""

import json
import sqlite3
from pathlib import Path

from poneglyph.config import settings

_SCHEMA_SQL = """
-- Topics: user-defined research areas with keywords and steering
CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    keywords        TEXT NOT NULL DEFAULT '[]',          -- JSON list of strings
    priority_keywords TEXT NOT NULL DEFAULT '[]',        -- JSON list of high-weight keywords
    problem_statements TEXT NOT NULL DEFAULT '[]',       -- JSON list of free-text problems
    sources         TEXT NOT NULL DEFAULT '["arxiv"]',   -- JSON list: arxiv, ssrn, kaggle
    pdf_policy      TEXT NOT NULL DEFAULT 'link_only'
                        CHECK (pdf_policy IN ('link_only', 'download')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Papers: metadata from any source
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,                       -- 'arxiv', 'ssrn', 'kaggle', 'manual'
    source_id       TEXT NOT NULL,                       -- e.g. arXiv ID
    semantic_scholar_id TEXT,                            -- Semantic Scholar paper ID for citation graph
    title           TEXT NOT NULL,
    authors         TEXT NOT NULL DEFAULT '[]',          -- JSON list
    published_venue TEXT NOT NULL DEFAULT '',
    published_date  TEXT,
    abstract        TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    pdf_url         TEXT NOT NULL DEFAULT '',
    pdf_local_path  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_id)
);

-- Many-to-many: topic <-> paper with relevance
CREATE TABLE IF NOT EXISTS topic_papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    matched_keywords TEXT NOT NULL DEFAULT '[]',         -- JSON list
    relevance_score REAL NOT NULL DEFAULT 0.0,
    recommendation  TEXT CHECK (recommendation IN ('read', 'skip', 'deep_dive')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(topic_id, paper_id)
);

-- Citation graph: tracks how papers are related
CREATE TABLE IF NOT EXISTS paper_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    to_paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    direction       TEXT NOT NULL CHECK (direction IN ('cites', 'cited_by')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_paper_id, to_paper_id, direction)
);

-- Structured notes per paper (one row per paper)
CREATE TABLE IF NOT EXISTS paper_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
    paper_info      TEXT NOT NULL DEFAULT '{}',          -- JSON
    key_insights    TEXT NOT NULL DEFAULT '{}',          -- JSON
    trading_applications TEXT NOT NULL DEFAULT '',
    abstract_excerpt TEXT NOT NULL DEFAULT '',
    recommendation  TEXT NOT NULL DEFAULT 'skip'
                        CHECK (recommendation IN ('read', 'skip', 'deep_dive')),
    human_note      TEXT,
    model_used      TEXT NOT NULL DEFAULT '',
    tier            TEXT NOT NULL DEFAULT 'bulk'
                        CHECK (tier IN ('bulk', 'deep')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cross-paper synthesis per topic
CREATE TABLE IF NOT EXISTS cross_syntheses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    paper_ids       TEXT NOT NULL DEFAULT '[]',          -- JSON list of paper IDs
    synthesis       TEXT NOT NULL DEFAULT '',
    research_directions TEXT NOT NULL DEFAULT '[]',      -- JSON list
    model_used      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Scouting run log
CREATE TABLE IF NOT EXISTS scout_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    source          TEXT NOT NULL,
    papers_found    INTEGER NOT NULL DEFAULT 0,
    papers_new      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',
    error_message   TEXT,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT
);

-- Topic steering change log
CREATE TABLE IF NOT EXISTS topic_steering_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    change_description TEXT NOT NULL,
    changed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 virtual table for full-text keyword search across papers
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title,
    abstract,
    content='papers',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync with papers table
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.id, new.title, new.abstract);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.id, new.title, new.abstract);
END;
"""


def _ensure_data_dir() -> None:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and FK enforcement."""
    _ensure_data_dir()
    conn = sqlite3.connect(settings.database_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Run lightweight migrations for schema changes on existing DBs."""
    # Add human_note to paper_notes if missing
    pn_cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_notes)").fetchall()}
    if "human_note" not in pn_cols:
        conn.execute("ALTER TABLE paper_notes ADD COLUMN human_note TEXT")

    # Add semantic_scholar_id to papers if missing
    p_cols = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "semantic_scholar_id" not in p_cols:
        conn.execute("ALTER TABLE papers ADD COLUMN semantic_scholar_id TEXT")

    # Add recommendation to topic_papers if missing
    tp_cols = {row[1] for row in conn.execute("PRAGMA table_info(topic_papers)").fetchall()}
    if "recommendation" not in tp_cols:
        conn.execute("ALTER TABLE topic_papers ADD COLUMN recommendation TEXT CHECK (recommendation IN ('read', 'skip', 'deep_dive'))")

    conn.commit()


def init_db() -> None:
    """Create all tables and virtual tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()


# ---------- convenience helpers used by routes ----------

def fetch_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Execute a write query and return lastrowid."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def executemany(sql: str, params_list: list[tuple]) -> None:
    conn = get_connection()
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, parsing JSON fields."""
    if row is None:
        return None
    d = dict(row)
    # Parse known JSON columns
    for key in ("keywords", "priority_keywords", "problem_statements",
                "sources", "authors", "matched_keywords", "paper_ids",
                "research_directions", "paper_info", "key_insights"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
