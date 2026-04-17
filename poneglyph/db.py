"""SQLite database setup with schema, FTS5, and connection management."""

import json
import sqlite3
from pathlib import Path

from poneglyph.config import settings

_SCHEMA_SQL = """
-- Topics: user-defined research areas with keywords, steering, and LLM skill prompts
CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    keywords        TEXT NOT NULL DEFAULT '[]',          -- JSON list of strings
    priority_keywords TEXT NOT NULL DEFAULT '[]',        -- JSON list of high-weight keywords
    problem_statements TEXT NOT NULL DEFAULT '[]',       -- JSON list of free-text problems
    sources         TEXT NOT NULL DEFAULT '[]',           -- reserved, unused
    is_active       INTEGER NOT NULL DEFAULT 1,
    skim_skill_md   TEXT,                                -- Haiku structural skim prompt (Markdown)
    deep_synthesis_skill_md TEXT,                        -- Sonnet/Opus deep synthesis prompt (Markdown)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Papers: metadata from any source
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,                       -- 'arxiv', 'ssrn', 'kaggle', 'manual'
    source_id       TEXT NOT NULL,                       -- e.g. arXiv ID
    semantic_scholar_id TEXT,                            -- Semantic Scholar paper ID for citation graph
    read_next       INTEGER NOT NULL DEFAULT 0,         -- user flag: 1 = read next
    unprocessed     INTEGER NOT NULL DEFAULT 1,         -- user flag: 1 = needs review, 0 = processed
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
    is_scout_seed   INTEGER NOT NULL DEFAULT 0,          -- 1 = used as seed in Scout Now / scheduler
    not_interesting INTEGER NOT NULL DEFAULT 0,          -- 1 = user marked not relevant for this topic
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

-- Paper-level notes (shared across topics)
CREATE TABLE IF NOT EXISTS paper_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
    paper_info      TEXT NOT NULL DEFAULT '{}',          -- JSON
    abstract_excerpt TEXT NOT NULL DEFAULT '',
    human_note      TEXT,                                -- user annotation, shared across topics
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-(paper, topic) structural skim + deep synthesis outputs
CREATE TABLE IF NOT EXISTS topic_paper_notes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    paper_id                INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    -- Structural skim: Pass 1 (Orientation)
    main_claim              TEXT NOT NULL DEFAULT '',
    data_source             TEXT NOT NULL DEFAULT '',
    strategy_type           TEXT NOT NULL DEFAULT '',
    headline_statistic      TEXT NOT NULL DEFAULT '',
    -- Structural skim: Pass 2 (Structural skim)
    signal_mechanism        TEXT NOT NULL DEFAULT '',
    data_details            TEXT NOT NULL DEFAULT '',
    sample                  TEXT NOT NULL DEFAULT '',
    universe                TEXT NOT NULL DEFAULT '',
    portfolio_construction  TEXT NOT NULL DEFAULT '',
    key_tables              TEXT NOT NULL DEFAULT '[]', -- JSON list
    key_metrics             TEXT NOT NULL DEFAULT '',
    skim_recommendation     TEXT CHECK (skim_recommendation IN ('read', 'skip', 'deep_dive')),
    skim_model_used         TEXT NOT NULL DEFAULT '',
    skim_skill_hash         TEXT NOT NULL DEFAULT '', -- SHA-256 of skill at generation time
    skim_generated_at       TEXT,
    skim_pdf_used           INTEGER NOT NULL DEFAULT 0, -- 1 = PDF sections used; 0 = abstract only
    -- Deep synthesis (Phase 4)
    deep_synthesis          TEXT,
    deep_synthesis_model_used TEXT NOT NULL DEFAULT '',
    deep_skill_hash         TEXT NOT NULL DEFAULT '',
    deep_generated_at       TEXT,
    UNIQUE(topic_id, paper_id)
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

-- Paper embeddings (sentence-transformers, float32 blob)
CREATE TABLE IF NOT EXISTS paper_embeddings (
    paper_id    INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL
);

-- Topic problem-statement embeddings (one row per PS)
CREATE TABLE IF NOT EXISTS topic_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    ps_index    INTEGER NOT NULL,
    embedding   BLOB NOT NULL,
    UNIQUE(topic_id, ps_index)
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
    if "read_next" not in p_cols:
        conn.execute("ALTER TABLE papers ADD COLUMN read_next INTEGER NOT NULL DEFAULT 0")
    if "unprocessed" not in p_cols:
        conn.execute("ALTER TABLE papers ADD COLUMN unprocessed INTEGER NOT NULL DEFAULT 1")

    # Add recommendation to topic_papers if missing
    tp_cols = {row[1] for row in conn.execute("PRAGMA table_info(topic_papers)").fetchall()}
    if "recommendation" not in tp_cols:
        conn.execute("ALTER TABLE topic_papers ADD COLUMN recommendation TEXT CHECK (recommendation IN ('read', 'skip', 'deep_dive'))")
    if "is_scout_seed" not in tp_cols:
        conn.execute("ALTER TABLE topic_papers ADD COLUMN is_scout_seed INTEGER NOT NULL DEFAULT 0")
    if "not_interesting" not in tp_cols:
        conn.execute("ALTER TABLE topic_papers ADD COLUMN not_interesting INTEGER NOT NULL DEFAULT 0")

    # Add skim_pdf_used to topic_paper_notes if missing
    tpn_cols = {row[1] for row in conn.execute("PRAGMA table_info(topic_paper_notes)").fetchall()}
    if "skim_pdf_used" not in tpn_cols:
        conn.execute("ALTER TABLE topic_paper_notes ADD COLUMN skim_pdf_used INTEGER NOT NULL DEFAULT 0")

    # Add skill columns to topics if missing
    t_cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    if "skim_skill_md" not in t_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN skim_skill_md TEXT")
    if "deep_synthesis_skill_md" not in t_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN deep_synthesis_skill_md TEXT")

    # Create topic_paper_notes table if missing (new per-(paper,topic) synthesis storage)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "topic_paper_notes" not in tables:
        conn.executescript("""
            CREATE TABLE topic_paper_notes (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id                INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                paper_id                INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                main_claim              TEXT NOT NULL DEFAULT '',
                data_source             TEXT NOT NULL DEFAULT '',
                strategy_type           TEXT NOT NULL DEFAULT '',
                headline_statistic      TEXT NOT NULL DEFAULT '',
                signal_mechanism        TEXT NOT NULL DEFAULT '',
                data_details            TEXT NOT NULL DEFAULT '',
                sample                  TEXT NOT NULL DEFAULT '',
                universe                TEXT NOT NULL DEFAULT '',
                portfolio_construction  TEXT NOT NULL DEFAULT '',
                key_tables              TEXT NOT NULL DEFAULT '[]',
                key_metrics             TEXT NOT NULL DEFAULT '',
                skim_recommendation     TEXT CHECK (skim_recommendation IN ('read', 'skip', 'deep_dive')),
                skim_model_used         TEXT NOT NULL DEFAULT '',
                skim_skill_hash         TEXT NOT NULL DEFAULT '',
                skim_generated_at       TEXT,
                deep_synthesis          TEXT,
                deep_synthesis_model_used TEXT NOT NULL DEFAULT '',
                deep_skill_hash         TEXT NOT NULL DEFAULT '',
                deep_generated_at       TEXT,
                UNIQUE(topic_id, paper_id)
            );
        """)

    # Migrate existing skim data from paper_notes → topic_paper_notes (best-effort, one-time)
    # Only runs when topic_paper_notes is empty and paper_notes has skim data
    pn_cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_notes)").fetchall()}
    has_old_skim = "main_claim" in pn_cols
    tpn_empty = conn.execute("SELECT COUNT(*) FROM topic_paper_notes").fetchone()[0] == 0
    if has_old_skim and tpn_empty:
        # For each paper with skim data, copy into topic_paper_notes for each linked topic
        rows = conn.execute(
            """SELECT pn.paper_id, pn.main_claim, pn.data_source, pn.strategy_type,
                      pn.headline_statistic, pn.signal_mechanism, pn.data_details,
                      pn.sample, pn.universe, pn.portfolio_construction, pn.key_tables,
                      pn.key_metrics, pn.recommendation, pn.model_used
               FROM paper_notes pn
               WHERE pn.main_claim != '' OR pn.signal_mechanism != ''"""
        ).fetchall()
        for row in rows:
            paper_id = row[0]
            topic_links = conn.execute(
                "SELECT topic_id FROM topic_papers WHERE paper_id = ?", (paper_id,)
            ).fetchall()
            for tlink in topic_links:
                topic_id = tlink[0]
                conn.execute(
                    """INSERT OR IGNORE INTO topic_paper_notes
                       (topic_id, paper_id, main_claim, data_source, strategy_type,
                        headline_statistic, signal_mechanism, data_details, sample,
                        universe, portfolio_construction, key_tables, key_metrics,
                        skim_recommendation, skim_model_used)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        topic_id, paper_id,
                        row[1], row[2], row[3], row[4], row[5], row[6],
                        row[7], row[8], row[9], row[10], row[11],
                        row[12], row[13] or "",
                    ),
                )

    # Drop legacy structural-skim columns from paper_notes (moved to topic_paper_notes)
    pn_cols_now = {row[1] for row in conn.execute("PRAGMA table_info(paper_notes)").fetchall()}
    legacy_skim_cols = {
        "main_claim", "data_source", "strategy_type", "headline_statistic",
        "signal_mechanism", "data_details", "sample", "universe",
        "portfolio_construction", "key_tables", "key_metrics",
        "recommendation", "model_used", "generated_at",
    }
    if pn_cols_now & legacy_skim_cols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paper_notes_clean (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id         INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
                paper_info       TEXT NOT NULL DEFAULT '{}',
                abstract_excerpt TEXT NOT NULL DEFAULT '',
                human_note       TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO paper_notes_clean
                (id, paper_id, paper_info, abstract_excerpt, human_note, created_at, updated_at)
            SELECT id, paper_id, paper_info, abstract_excerpt, human_note, created_at, updated_at
            FROM paper_notes;
            DROP TABLE paper_notes;
            ALTER TABLE paper_notes_clean RENAME TO paper_notes;
        """)

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
                "research_directions", "paper_info", "key_tables"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
