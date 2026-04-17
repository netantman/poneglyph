"""Sentence-transformer embeddings — lazy-loaded singleton model."""
import logging
import numpy as np

logger = logging.getLogger(__name__)

_model = None
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s...", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model loaded.")
    return _model


def encode(texts: list[str]) -> np.ndarray:
    """Encode a list of texts to a 2D float32 array (N x DIM), L2-normalised."""
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def embed_paper(paper: dict) -> np.ndarray:
    """Embed title + abstract concatenated."""
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    text = f"{title}. {abstract}" if abstract else title
    if not text:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    return encode([text])[0]


def embed_problem_statements(topic: dict) -> list[np.ndarray]:
    """Embed each problem statement separately; returns list of vectors."""
    ps_list = topic.get("problem_statements") or []
    if not ps_list:
        return []
    return list(encode(ps_list))


def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()
