"""
Query understanding + vector retrieval + cross-encoder reranking.
"""
import re
from typing import List, Optional, Tuple

from backend.services.embedding_service import load_index, embed_query
from backend.services.chunking_service import Chunk
from backend.utils.config import (
    EMBEDDING_PROVIDER, RERANKER_MODEL_NAME, TOP_K_RETRIEVE, TOP_N_RERANK,
)

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL_NAME)
    return _reranker


# --------------------------- Query understanding ------------------------------

_FILE_HINT_RE = re.compile(r'([A-Za-z0-9_\-/]+\.[A-Za-z]{1,5})')
_STOPWORDS = {"the", "a", "an", "is", "are", "how", "does", "do", "what", "in", "of", "to", "for"}


def understand_query(query: str) -> dict:
    """
    Light-weight query understanding: detect explicit file mentions and
    pull out salient keywords for optional lexical boosting.
    """
    query = query.strip()
    file_hints = _FILE_HINT_RE.findall(query)
    keywords = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
                if w.lower() not in _STOPWORDS]
    intent = "general"
    lower = query.lower()
    if any(k in lower for k in [
        "how many files", "total files", "list all files", "list the files",
        "list them", "what files are there", "show me the files", "file count",
        "number of files",
    ]):
        intent = "list_files"
    elif any(k in lower for k in ["architecture", "structure", "overview", "how is this organized"]):
        intent = "architecture"
    elif any(k in lower for k in ["explain", "what does", "what is"]) and file_hints:
        intent = "explain_file"
    elif any(k in lower for k in ["bug", "error", "fix", "why does"]):
        intent = "debugging"

    return {"clean_query": query, "file_hints": file_hints, "keywords": keywords, "intent": intent}


# --------------------------- Retrieval ----------------------------------------

def retrieve(repo_id: str, query: str, top_k: int = TOP_K_RETRIEVE) -> List[Tuple[Chunk, float]]:
    index, chunks = load_index(repo_id)
    q_vec = embed_query(query)
    top_k = min(top_k, len(chunks))
    scores, idxs = index.search(q_vec, top_k)

    results = []
    for score, idx in zip(scores[0], idxs[0]):
        if idx == -1:
            continue
        results.append((chunks[idx], float(score)))

    # Simple lexical boost: if a file hinted in the query matches the chunk's
    # file path, nudge its score up slightly before reranking.
    qinfo = understand_query(query)
    if qinfo["file_hints"]:
        boosted = []
        for chunk, score in results:
            boost = 0.05 if any(h.lower() in chunk.file_path.lower() for h in qinfo["file_hints"]) else 0.0
            boosted.append((chunk, score + boost))
        results = boosted

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def rerank(query: str, candidates: List[Tuple[Chunk, float]], top_n: int = TOP_N_RERANK) -> List[Tuple[Chunk, float]]:
    if not candidates:
        return []
    if EMBEDDING_PROVIDER == "hashing":
        return candidates[:top_n]
    reranker = get_reranker()
    pairs = [[query, c.content] for c, _ in candidates]
    cross_scores = reranker.predict(pairs)
    scored = list(zip([c for c, _ in candidates], cross_scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def retrieve_and_rerank(repo_id: str, query: str) -> List[Tuple[Chunk, float]]:
    candidates = retrieve(repo_id, query, top_k=TOP_K_RETRIEVE)
    return rerank(query, candidates, top_n=TOP_N_RERANK)
