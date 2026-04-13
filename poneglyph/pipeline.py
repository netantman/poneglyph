"""Scouting pipeline: citation discovery + Haiku synthesis."""

import json
import logging

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.services.citation_scout import discover_from_paper
from poneglyph.services.llm_bulk import synthesize_paper

logger = logging.getLogger(__name__)

# Max papers to synthesize per run — keeps cost and duration predictable
MAX_SYNTH_PER_RUN = 30


# ---------- Run lifecycle ----------

def create_run(topic_id: int | None, source: str) -> int:
    return execute(
        "INSERT INTO scout_runs (topic_id, source) VALUES (?, ?)",
        (topic_id, source),
    )


def _finish_run(run_id: int, *, found: int, new: int, status: str = "ok", error: str = "") -> None:
    execute(
        """UPDATE scout_runs
           SET papers_found = ?, papers_new = ?, status = ?, error_message = ?,
               finished_at = datetime('now')
           WHERE id = ?""",
        (found, new, status, error or None, run_id),
    )


# ---------- Synthesis helper ----------

async def _synthesize_paper(paper_id: int, topic: dict) -> bool:
    """Run Haiku synthesis for one paper and persist results. Returns True on success."""
    paper = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (paper_id,)))
    if not paper:
        return False

    # Gather human notes from other papers in the topic for context (most recently annotated)
    note_rows = fetch_all(
        """SELECT pn.human_note FROM paper_notes pn
           JOIN topic_papers tp ON tp.paper_id = pn.paper_id
           WHERE tp.topic_id = ? AND pn.paper_id != ?
           AND pn.human_note IS NOT NULL AND pn.human_note != ''
           ORDER BY pn.updated_at DESC
           LIMIT 5""",
        (topic["id"], paper_id),
    )
    related_notes = [r["human_note"] for r in note_rows]

    result = await synthesize_paper(paper, topic, related_notes)
    if not result:
        return False

    execute(
        """UPDATE paper_notes
           SET key_insights = ?, trading_applications = ?, recommendation = ?,
               model_used = 'claude-haiku-4-5-20251001', updated_at = datetime('now')
           WHERE paper_id = ?""",
        (
            json.dumps(result["key_insights"]),
            result["trading_applications"],
            result["recommendation"],
            paper_id,
        ),
    )

    # Also store recommendation in topic_papers for the list view
    execute(
        "UPDATE topic_papers SET recommendation = ? WHERE topic_id = ? AND paper_id = ?",
        (result["recommendation"], topic["id"], paper_id),
    )
    return True


# ---------- Public pipeline entry points ----------

async def run_paper_enrichment(paper_id: int, topic_id: int, run_id: int) -> None:
    """Discover citations/references for one paper, synthesize new ones.

    Updates scout_runs row when done. Never raises.
    """
    try:
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        new_ids = await discover_from_paper(paper_id, topic_id)
        execute("UPDATE scout_runs SET papers_found = ? WHERE id = ?", (len(new_ids), run_id))

        synth_ids = new_ids[:MAX_SYNTH_PER_RUN]
        synth_count = 0
        for pid in synth_ids:
            if await _synthesize_paper(pid, topic):
                synth_count += 1

        _finish_run(run_id, found=len(new_ids), new=synth_count)
        logger.info(
            "run_paper_enrichment done: paper=%d topic=%d found=%d synth=%d",
            paper_id, topic_id, len(new_ids), synth_count,
        )
    except Exception as exc:
        logger.exception("run_paper_enrichment failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))


async def run_topic_scout(topic_id: int, run_id: int) -> None:
    """Discover citations for seed papers in a topic, then synthesize new ones.

    Only papers with is_scout_seed=1 are used as traversal starting points.
    Updates scout_runs row when done. Never raises.
    """
    try:
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            _finish_run(run_id, found=0, new=0, status="error", error="Topic not found")
            return

        paper_rows = fetch_all(
            "SELECT paper_id FROM topic_papers WHERE topic_id = ? AND is_scout_seed = 1",
            (topic_id,),
        )
        paper_ids = [r["paper_id"] for r in paper_rows]

        if not paper_ids:
            logger.warning("run_topic_scout: topic=%d has no seed papers — skipping", topic_id)
            _finish_run(run_id, found=0, new=0, status="no_seeds")
            return

        all_new: set[int] = set()
        for pid in paper_ids:
            new = await discover_from_paper(pid, topic_id)
            all_new.update(new)
            # Update running count
            execute(
                "UPDATE scout_runs SET papers_found = ? WHERE id = ?",
                (len(all_new), run_id),
            )

        synth_ids = list(all_new)[:MAX_SYNTH_PER_RUN]
        synth_count = 0
        for pid in synth_ids:
            if await _synthesize_paper(pid, topic):
                synth_count += 1

        _finish_run(run_id, found=len(all_new), new=synth_count)
        logger.info(
            "run_topic_scout done: topic=%d seeds=%d found=%d synth=%d",
            topic_id, len(paper_ids), len(all_new), synth_count,
        )
    except Exception as exc:
        logger.exception("run_topic_scout failed: %s", exc)
        _finish_run(run_id, found=0, new=0, status="error", error=str(exc))
