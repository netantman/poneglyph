"""Relevance scoring and semantic search using paper/topic embeddings."""
import logging
import numpy as np

from poneglyph.db import execute, fetch_all, fetch_one, row_to_dict
from poneglyph.services.embeddings import (
    embed_paper, embed_problem_statements, from_blob, to_blob,
)

logger = logging.getLogger(__name__)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def _get_or_create_paper_embedding(paper_id: int, paper: dict) -> np.ndarray:
    row = fetch_one("SELECT embedding FROM paper_embeddings WHERE paper_id = ?", (paper_id,))
    if row and row["embedding"]:
        return from_blob(row["embedding"])
    vec = embed_paper(paper)
    execute(
        "INSERT OR REPLACE INTO paper_embeddings (paper_id, embedding) VALUES (?, ?)",
        (paper_id, to_blob(vec)),
    )
    return vec


def _get_or_create_topic_embeddings(topic_id: int, topic: dict) -> list[np.ndarray]:
    rows = fetch_all(
        "SELECT embedding FROM topic_embeddings WHERE topic_id = ? ORDER BY ps_index",
        (topic_id,),
    )
    if rows:
        return [from_blob(r["embedding"]) for r in rows]
    return _recompute_topic_embeddings(topic_id, topic)


def _recompute_topic_embeddings(topic_id: int, topic: dict) -> list[np.ndarray]:
    execute("DELETE FROM topic_embeddings WHERE topic_id = ?", (topic_id,))
    vecs = embed_problem_statements(topic)
    for i, vec in enumerate(vecs):
        execute(
            "INSERT INTO topic_embeddings (topic_id, ps_index, embedding) VALUES (?, ?, ?)",
            (topic_id, i, to_blob(vec)),
        )
    return vecs


def refresh_topic_embeddings(topic_id: int, topic: dict) -> None:
    """Force-recompute topic embeddings (call when problem statements change)."""
    _recompute_topic_embeddings(topic_id, topic)


def _score(paper_emb: np.ndarray, ps_embeddings: list[np.ndarray]) -> float:
    if not ps_embeddings:
        return 0.0
    return max(_cosine(paper_emb, ps) for ps in ps_embeddings)


def update_topic_relevance_scores(topic_id: int) -> int:
    """Recompute relevance_score for all papers in a topic. Returns count updated."""
    topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
    if not topic:
        return 0
    ps_embeddings = _get_or_create_topic_embeddings(topic_id, topic)
    if not ps_embeddings:
        logger.info("topic %d has no problem statements — skipping relevance scoring", topic_id)
        return 0
    paper_rows = fetch_all(
        "SELECT p.id, p.title, p.abstract FROM papers p "
        "JOIN topic_papers tp ON p.id = tp.paper_id WHERE tp.topic_id = ?",
        (topic_id,),
    )
    updated = 0
    for row in paper_rows:
        paper = dict(row)
        paper_id = paper["id"]
        vec = _get_or_create_paper_embedding(paper_id, paper)
        score = _score(vec, ps_embeddings)
        execute(
            "UPDATE topic_papers SET relevance_score = ? WHERE topic_id = ? AND paper_id = ?",
            (score, topic_id, paper_id),
        )
        updated += 1
    logger.info("update_topic_relevance_scores: topic=%d updated=%d", topic_id, updated)
    return updated


def update_paper_all_topic_scores(paper_id: int, paper: dict) -> None:
    """Recompute relevance_score for one paper across all its associated topics."""
    vec = _get_or_create_paper_embedding(paper_id, paper)
    topic_rows = fetch_all(
        "SELECT t.id FROM topics t "
        "JOIN topic_papers tp ON t.id = tp.topic_id WHERE tp.paper_id = ?",
        (paper_id,),
    )
    for row in topic_rows:
        topic_id = row["id"]
        topic = row_to_dict(fetch_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        if not topic:
            continue
        ps_embeddings = _get_or_create_topic_embeddings(topic_id, topic)
        if not ps_embeddings:
            continue
        score = _score(vec, ps_embeddings)
        execute(
            "UPDATE topic_papers SET relevance_score = ? WHERE topic_id = ? AND paper_id = ?",
            (score, topic_id, paper_id),
        )


def semantic_search(query: str, top_k: int = 20) -> list[dict]:
    """Find top-k papers by semantic similarity to the query string."""
    from poneglyph.services.embeddings import encode
    q_vec = encode([query])[0]
    rows = fetch_all("SELECT paper_id, embedding FROM paper_embeddings")
    if not rows:
        return []
    paper_ids = [r["paper_id"] for r in rows]
    matrix = np.stack([from_blob(r["embedding"]) for r in rows])
    scores = matrix @ q_vec  # cosine sim (vectors are L2-normalised)
    top_indices = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_indices:
        pid = paper_ids[int(idx)]
        score = float(scores[idx])
        p = row_to_dict(fetch_one("SELECT * FROM papers WHERE id = ?", (pid,)))
        if p:
            p["semantic_score"] = round(score, 3)
            results.append(p)
    return results
