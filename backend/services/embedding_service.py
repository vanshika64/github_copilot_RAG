"""
Generates embeddings for code chunks and manages a per-repository FAISS
index on disk (vectorstore/indexes/<repo_id>/).
"""
import json
import pickle
import hashlib
import re
from pathlib import Path
from typing import List, Optional

import numpy as np
import faiss

from backend.utils.config import (
    EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL_NAME, EMBEDDING_PROVIDER,
    HASH_EMBEDDING_DIM, INDEX_DIR,
)
from backend.services.chunking_service import Chunk

_model = None


def get_embedding_model():
    """Load the optional transformer only when explicitly enabled."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _hash_embedding(text: str) -> np.ndarray:
    """Create a stable normalized lexical vector without an ML model."""
    vector = np.zeros(HASH_EMBEDDING_DIM, dtype="float32")
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text.lower()):
        value = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
        )
        vector[value % HASH_EMBEDDING_DIM] += 1.0 if value & 1 else -1.0
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def embed_texts(texts: List[str]) -> np.ndarray:
    if EMBEDDING_PROVIDER == "hashing":
        return np.asarray([_hash_embedding(text) for text in texts], dtype="float32")
    if EMBEDDING_PROVIDER != "sentence-transformers":
        raise ValueError("EMBEDDING_PROVIDER must be 'hashing' or 'sentence-transformers'.")

    model = get_embedding_model()
    vecs = model.encode(
        texts, batch_size=EMBEDDING_BATCH_SIZE, show_progress_bar=False,
        convert_to_numpy=True, normalize_embeddings=True,
    )
    return vecs.astype("float32")


def _index_paths(repo_id: str):
    repo_dir = INDEX_DIR / repo_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    return repo_dir / "index.faiss", repo_dir / "meta.pkl", repo_dir / "stats.json"


def build_and_save_index(repo_id: str, chunks: List[Chunk]) -> None:
    faiss_path, meta_path, _ = _index_paths(repo_id)

    if not chunks:
        raise ValueError("No chunks to index.")

    # Add vectors incrementally so transformer mode also has a bounded peak
    # memory footprint.
    index = None
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start:start + EMBEDDING_BATCH_SIZE]
        embeddings = embed_texts([chunk.content for chunk in batch])
        if index is None:
            index = faiss.IndexFlatIP(embeddings.shape[1])  # cosine similarity
        index.add(embeddings)

    faiss.write_index(index, str(faiss_path))

    with open(meta_path, "wb") as f:
        pickle.dump(chunks, f)


def load_index(repo_id: str):
    faiss_path, meta_path, _ = _index_paths(repo_id)
    if not faiss_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"No index found for repo_id={repo_id}")
    index = faiss.read_index(str(faiss_path))
    with open(meta_path, "rb") as f:
        chunks: List[Chunk] = pickle.load(f)
    return index, chunks


def index_exists(repo_id: str) -> bool:
    faiss_path, meta_path, _ = _index_paths(repo_id)
    return faiss_path.exists() and meta_path.exists()


def save_stats(repo_id: str, stats: dict) -> None:
    _, _, stats_path = _index_paths(repo_id)
    with open(stats_path, "w") as f:
        json.dump(stats, f)


def load_stats(repo_id: str) -> dict:
    _, _, stats_path = _index_paths(repo_id)
    if not stats_path.exists():
        return {}
    with open(stats_path) as f:
        return json.load(f)


def embed_query(query: str) -> np.ndarray:
    return embed_texts([query])
