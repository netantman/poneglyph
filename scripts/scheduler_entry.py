"""Poneglyph scheduler entry point.

Modes:
  --mode article-scout   Daily: RSS poll for subscribed authors per topic
  --mode scout           Weekly: citation-graph scouting from seed papers
  --mode cross-synthesis Monthly: cross-paper synthesis for all topics

Usage:
    python scripts/scheduler_entry.py --mode article-scout
    python scripts/scheduler_entry.py --mode scout
    python scripts/scheduler_entry.py --mode cross-synthesis
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler_entry")


async def _run_article_scout() -> None:
    from poneglyph.db import init_db
    from poneglyph.pipeline import run_all_article_scouts

    init_db()
    logger.info("article-scout: starting")
    await run_all_article_scouts()
    logger.info("article-scout: done")


async def _run_scout() -> None:
    from poneglyph.db import fetch_all, init_db, row_to_dict
    from poneglyph.pipeline import create_run, run_topic_scout

    init_db()
    logger.info("scout: starting citation-graph scout for all active topics")

    topic_rows = fetch_all(
        "SELECT id FROM topics WHERE is_active = 1 ORDER BY id", ()
    )
    if not topic_rows:
        logger.info("scout: no active topics")
        return

    for row in topic_rows:
        tid = row["id"]
        seed_count = fetch_all(
            "SELECT COUNT(*) as n FROM topic_papers WHERE topic_id=? AND is_scout_seed=1",
            (tid,),
        )
        if not seed_count or seed_count[0]["n"] == 0:
            logger.info("scout: topic=%d has no seeds — skipping", tid)
            continue
        run_id = create_run(tid, "topic_scout_scheduled")
        await run_topic_scout(tid, run_id)

    logger.info("scout: done")


async def _run_cross_synthesis() -> None:
    from poneglyph.db import fetch_all, fetch_one, init_db, row_to_dict
    from poneglyph.pipeline import create_run, _finish_run
    from poneglyph.services.llm_cross import cross_synthesize
    from poneglyph.config import settings
    import json

    init_db()
    logger.info("cross-synthesis: starting for all active topics")

    topic_rows = fetch_all("SELECT id FROM topics WHERE is_active = 1 ORDER BY id", ())
    for row in topic_rows:
        tid = row["id"]
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id=?", (tid,)))
        if not topic:
            continue

        paper_rows = fetch_all(
            """SELECT p.*, pn.human_note
               FROM papers p
               JOIN topic_papers tp ON p.id = tp.paper_id
               LEFT JOIN paper_notes pn ON pn.paper_id = p.id
               WHERE tp.topic_id = ?
               ORDER BY COALESCE(tp.relevance_score, 0.0) DESC""",
            (tid,),
        )
        if not paper_rows:
            logger.info("cross-synthesis: topic=%d has no papers — skipping", tid)
            continue

        from poneglyph.db import execute
        paper_notes: list[dict] = []
        paper_ids: list[int] = []
        for prow in paper_rows:
            p = row_to_dict(prow)
            pid = p["id"]
            paper_ids.append(pid)
            skim_rows = fetch_all(
                "SELECT * FROM topic_paper_notes WHERE topic_id=? AND paper_id=? LIMIT 1",
                (tid, pid),
            )
            skim = row_to_dict(skim_rows[0]) if skim_rows else None
            paper_notes.append({"paper": p, "skim": skim, "human_note": p.get("human_note")})

        run_id = create_run(tid, "cross_synthesis_scheduled")
        try:
            synthesis, directions = await cross_synthesize(topic, paper_notes)
            execute(
                """INSERT INTO cross_syntheses
                   (topic_id, paper_ids, synthesis, research_directions, model_used)
                   VALUES (?, ?, ?, ?, ?)""",
                (tid, json.dumps(paper_ids), synthesis, json.dumps(directions), settings.sonnet_model),
            )
            _finish_run(run_id, found=len(paper_ids), new=1)
            logger.info("cross-synthesis: topic=%d done (%d sources)", tid, len(paper_ids))
        except Exception as exc:
            logger.error("cross-synthesis: topic=%d failed: %s", tid, exc)
            _finish_run(run_id, found=0, new=0, status="error", error=str(exc)[:500])

    logger.info("cross-synthesis: done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Poneglyph scheduler entry point")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["article-scout", "scout", "cross-synthesis"],
        help="Which scheduled job to run",
    )
    args = parser.parse_args()

    if args.mode == "article-scout":
        asyncio.run(_run_article_scout())
    elif args.mode == "scout":
        asyncio.run(_run_scout())
    elif args.mode == "cross-synthesis":
        asyncio.run(_run_cross_synthesis())


if __name__ == "__main__":
    main()
