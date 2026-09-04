"""
Generates embeddings for code chunks and manages a per-repository FAISS
index on disk (vectorstore/indexes/<repo_id>/).
"""
import json
import pickle
from pathlib import Path
from typing import List, Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from backend.utils.config import EMBEDDING_MODEL_NAME, INDEX_DIR
from backend.services.chunking_service import Chunk

_model: Optional[SentenceTransformer] = None


def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: List[str]) -> np.ndarray:
    model = get_embedding_model()
    vecs = model.encode(
        texts, batch_size=32, show_progress_bar=False,
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

    embeddings = embed_texts([c.content for c in chunks])
    dim = embeddings.shape[1]

    index = faiss.IndexFlatIP(dim)  # cosine sim via normalized vectors + inner product
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
