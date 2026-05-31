"""SQLite database setup with schema, FTS5, and connection management."""

import json
import logging
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from poneglyph.config import settings

logger = logging.getLogger(__name__)

# Bump this whenever a destructive migration is added. _migrate() compares against
# PRAGMA user_version and only runs newer steps, so we don't re-execute migrations
# on every boot.
SCHEMA_VERSION = 7

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
    skim_field_labels TEXT,                              -- JSON dict of display-label overrides for skim fields
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
    onenote_url     TEXT,
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

-- Paper-level notes (metadata only — human notes moved to topic_paper_notes)
CREATE TABLE IF NOT EXISTS paper_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
    paper_info      TEXT NOT NULL DEFAULT '{}',          -- JSON
    abstract_excerpt TEXT NOT NULL DEFAULT '',
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
    -- Per-topic human note (Phase 7)
    human_note              TEXT,
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

-- Q&A history: saved questions and generated answers
CREATE TABLE IF NOT EXISTS qa_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer_md   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Authors: global library of authors and aggregators to scout
CREATE TABLE IF NOT EXISTS authors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    byline          TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT 'author',    -- 'author' | 'aggregator' | 'stub'
    source_origin   TEXT NOT NULL DEFAULT 'manual',    -- 'manual' | 'aggregator_dereference'
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);

-- Author sources: one author can have multiple feed URLs
CREATE TABLE IF NOT EXISTS author_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id       INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL DEFAULT 'rss',       -- 'rss' | 'newsletter' | 'scrape' | 'manual'
    url             TEXT NOT NULL,
    verified_at     TEXT,
    last_polled_at  TEXT,
    last_status     TEXT NOT NULL DEFAULT 'unverified', -- 'ok' | 'http_error' | 'parse_error' | 'unverified'
    last_error      TEXT,
    etag            TEXT,
    last_modified   TEXT,
    UNIQUE(author_id, url)
);

-- Topic-author subscriptions (many-to-many)
CREATE TABLE IF NOT EXISTS topic_authors (
    topic_id            INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    author_id           INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    added_at            TEXT NOT NULL DEFAULT (datetime('now')),
    active              INTEGER NOT NULL DEFAULT 1,
    scout_lookback_days INTEGER NOT NULL DEFAULT 30,
    PRIMARY KEY (topic_id, author_id)
);

-- Full-text cache for articles (and optionally PDF-extracted text)
CREATE TABLE IF NOT EXISTS paper_fulltext (
    paper_id        INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    body_text       TEXT NOT NULL DEFAULT '',
    body_html       TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'rss_full',  -- 'rss_full' | 'subscriber_rss' | 'manual_paste' | 'pdf_extract'
    cached_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pending skim queue for bulk add-to-topic (Phase 7)
CREATE TABLE IF NOT EXISTS pending_skims (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    queued_at TEXT NOT NULL DEFAULT (datetime('now')),
    status    TEXT NOT NULL DEFAULT 'pending',           -- 'pending' | 'done' | 'error'
    error_msg TEXT,
    UNIQUE(topic_id, paper_id)
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
    """Return a new SQLite connection with WAL mode, FK enforcement, and durability PRAGMAs."""
    _ensure_data_dir()
    conn = sqlite3.connect(settings.database_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # synchronous=NORMAL is durable under WAL and faster than FULL.
    conn.execute("PRAGMA synchronous=NORMAL")
    # Auto-checkpoint every 1000 pages so the WAL doesn't grow unbounded.
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    # Wait up to 5s if another writer (e.g. backup script) holds the lock.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction():
    """Context manager that yields a connection inside an explicit transaction.

    Commits on clean exit, rolls back on any exception, and always closes.
    Use this for any code path that runs more than one write — `_synthesize_paper`,
    delete handlers, etc.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _backup_before_migration(reason: str) -> None:
    """Copy the live DB file into data/migration_backups/ before a destructive migration.

    Keeps the last 5 backups by mtime. Best-effort — never raises.
    """
    src = Path(settings.database_path)
    if not src.exists():
        return
    backup_dir = src.parent / "migration_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"poneglyph-{reason}-{ts}.db"
    try:
        shutil.copy2(src, dest)
        logger.info("Migration backup written: %s", dest)
    except Exception as exc:
        logger.warning("Migration backup failed: %s", exc)
        return
    # Prune to 5 most-recent
    backups = sorted(backup_dir.glob("poneglyph-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[5:]:
        try:
            old.unlink()
        except OSError:
            pass


def _check_integrity(conn: sqlite3.Connection) -> None:
    """Run PRAGMA integrity_check; log loudly if it fails. Does not raise."""
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            logger.error(
                "DB integrity check FAILED: %s — investigate before continuing", result
            )
        else:
            logger.info("DB integrity check: ok")
    except Exception as exc:
        logger.warning("DB integrity check could not run: %s", exc)


def _migrate(conn: sqlite3.Connection) -> None:
    """Run lightweight migrations for schema changes on existing DBs.

    Skips work when PRAGMA user_version already matches SCHEMA_VERSION.
    Snapshots the DB before any destructive (DROP/RENAME) step.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

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

    # Add skim_pdf_used / skip_reason to topic_paper_notes if missing
    tpn_cols = {row[1] for row in conn.execute("PRAGMA table_info(topic_paper_notes)").fetchall()}
    if "skim_pdf_used" not in tpn_cols:
        conn.execute("ALTER TABLE topic_paper_notes ADD COLUMN skim_pdf_used INTEGER NOT NULL DEFAULT 0")
    if "skip_reason" not in tpn_cols:
        conn.execute("ALTER TABLE topic_paper_notes ADD COLUMN skip_reason TEXT NOT NULL DEFAULT ''")

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
        _backup_before_migration("drop_legacy_skim_cols")
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

    # Create qa_history table if missing (added in phase 4)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "qa_history" not in tables:
        conn.execute("""
            CREATE TABLE qa_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                question    TEXT NOT NULL,
                answer_md   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

    # ── Schema version 2: article / author scouting ──────────────────────────
    if current < 2:
        # New columns on papers
        p_cols_v2 = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        for col, defn in [
            ("content_type",   "TEXT NOT NULL DEFAULT 'academic'"),
            ("access_status",  "TEXT NOT NULL DEFAULT 'public'"),
            ("canonical_url",  "TEXT"),
            ("author_id",      "INTEGER"),
            ("body_fetched_at","TEXT"),
        ]:
            if col not in p_cols_v2:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {defn}")

        # New column on topics
        t_cols_v2 = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
        if "article_skim_skill_md" not in t_cols_v2:
            conn.execute("ALTER TABLE topics ADD COLUMN article_skim_skill_md TEXT")

        # New tables (if the DB predates _SCHEMA_SQL additions above)
        tables_v2 = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for ddl in [
            """CREATE TABLE IF NOT EXISTS authors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                byline        TEXT NOT NULL DEFAULT '',
                entity_type   TEXT NOT NULL DEFAULT 'author',
                source_origin TEXT NOT NULL DEFAULT 'manual',
                notes         TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name)
            )""",
            """CREATE TABLE IF NOT EXISTS author_sources (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id      INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
                source_type    TEXT NOT NULL DEFAULT 'rss',
                url            TEXT NOT NULL,
                verified_at    TEXT,
                last_polled_at TEXT,
                last_status    TEXT NOT NULL DEFAULT 'unverified',
                last_error     TEXT,
                etag           TEXT,
                last_modified  TEXT,
                UNIQUE(author_id, url)
            )""",
            """CREATE TABLE IF NOT EXISTS topic_authors (
                topic_id            INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                author_id           INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
                added_at            TEXT NOT NULL DEFAULT (datetime('now')),
                active              INTEGER NOT NULL DEFAULT 1,
                scout_lookback_days INTEGER NOT NULL DEFAULT 30,
                PRIMARY KEY (topic_id, author_id)
            )""",
            """CREATE TABLE IF NOT EXISTS paper_fulltext (
                paper_id  INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                body_text TEXT NOT NULL DEFAULT '',
                body_html TEXT NOT NULL DEFAULT '',
                source    TEXT NOT NULL DEFAULT 'rss_full',
                cached_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""",
        ]:
            conn.execute(ddl)

        # Indexes for article queries
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_papers_content_type   ON papers(content_type);
            CREATE INDEX IF NOT EXISTS idx_papers_canonical_url  ON papers(canonical_url);
            CREATE INDEX IF NOT EXISTS idx_papers_author_id      ON papers(author_id);
            CREATE INDEX IF NOT EXISTS idx_author_sources_polled ON author_sources(last_polled_at);
        """)

    # ── Schema version 3: per-topic human notes ──────────────────────────────
    if current < 3:
        # 1. Add human_note to topic_paper_notes (for existing DBs)
        tpn_cols_v3 = {row[1] for row in conn.execute("PRAGMA table_info(topic_paper_notes)").fetchall()}
        if "human_note" not in tpn_cols_v3:
            conn.execute("ALTER TABLE topic_paper_notes ADD COLUMN human_note TEXT")

        # 2. Backfill: copy paper_notes.human_note → existing topic_paper_notes rows
        conn.execute("""
            UPDATE topic_paper_notes
            SET human_note = (
                SELECT pn.human_note FROM paper_notes pn
                WHERE pn.paper_id = topic_paper_notes.paper_id
                  AND pn.human_note IS NOT NULL AND pn.human_note != ''
            )
            WHERE human_note IS NULL OR human_note = ''
        """)

        # 3. Stub-insert for papers with notes but no topic_paper_notes row yet
        conn.execute("""
            INSERT OR IGNORE INTO topic_paper_notes (topic_id, paper_id, human_note)
            SELECT tp.topic_id, tp.paper_id, pn.human_note
            FROM topic_papers tp
            JOIN paper_notes pn ON pn.paper_id = tp.paper_id
            WHERE pn.human_note IS NOT NULL AND pn.human_note != ''
        """)

        # 4. Ensure pending_skims table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_skims (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                queued_at TEXT NOT NULL DEFAULT (datetime('now')),
                status    TEXT NOT NULL DEFAULT 'pending',
                error_msg TEXT,
                UNIQUE(topic_id, paper_id)
            )
        """)

        # 5. Drop paper_notes.human_note via recreate-table dance
        pn_cols_v3 = {row[1] for row in conn.execute("PRAGMA table_info(paper_notes)").fetchall()}
        if "human_note" in pn_cols_v3:
            _backup_before_migration("drop_paper_notes_human_note")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS paper_notes_v3 (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id         INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
                    paper_info       TEXT NOT NULL DEFAULT '{}',
                    abstract_excerpt TEXT NOT NULL DEFAULT '',
                    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT OR IGNORE INTO paper_notes_v3
                    (id, paper_id, paper_info, abstract_excerpt, created_at, updated_at)
                SELECT id, paper_id, paper_info, abstract_excerpt, created_at, updated_at
                FROM paper_notes;
                DROP TABLE paper_notes;
                ALTER TABLE paper_notes_v3 RENAME TO paper_notes;
            """)

    # ── Schema version 5: per-paper Q&A history ──────────────────────────────
    if current < 5:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_qa_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_qa_history_paper_topic
                ON paper_qa_history (paper_id, topic_id)
        """)

    # ── Schema version 6: onenote_url on papers ──────────────────────────────
    if current < 6:
        p_cols_v6 = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "onenote_url" not in p_cols_v6:
            conn.execute("ALTER TABLE papers ADD COLUMN onenote_url TEXT")

    # ── Schema version 7: skim_field_labels on topics ────────────────────────
    if current < 7:
        t_cols_v7 = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
        if "skim_field_labels" not in t_cols_v7:
            conn.execute("ALTER TABLE topics ADD COLUMN skim_field_labels TEXT")

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db() -> None:
    """Create all tables and virtual tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _migrate(conn)
        _check_integrity(conn)
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
                "research_directions", "paper_info", "key_tables",
                "article_cross_references"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
