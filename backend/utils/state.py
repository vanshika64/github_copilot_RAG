"""
Simple in-memory state shared across requests (fine for a single-process
demo app; swap for Redis/DB in production).
"""
import threading
from typing import Dict, Any

_lock = threading.Lock()

# repo_id -> {stage, progress, message, done, error}
INDEX_STATUS: Dict[str, Dict[str, Any]] = {}

# repo_id -> metadata dict (owner, repo, url, github info, repo_root path, stats...)
REPO_META: Dict[str, Dict[str, Any]] = {}

# repo_id -> {summary, key_features, tech_stack, suggested_questions, architecture}
REPO_INSIGHTS: Dict[str, Dict[str, Any]] = {}


def set_status(repo_id: str, stage: str, progress: int, message: str, done: bool = False, error: str = None):
    with _lock:
        INDEX_STATUS[repo_id] = {
            "repo_id": repo_id, "stage": stage, "progress": progress,
            "message": message, "done": done, "error": error,
        }


def get_status(repo_id: str):
    with _lock:
        return INDEX_STATUS.get(repo_id)


def set_meta(repo_id: str, **kwargs):
    with _lock:
        REPO_META.setdefault(repo_id, {}).update(kwargs)


def get_meta(repo_id: str) -> dict:
    with _lock:
        return REPO_META.get(repo_id, {})


def set_insights(repo_id: str, **kwargs):
    with _lock:
        REPO_INSIGHTS.setdefault(repo_id, {}).update(kwargs)


def get_insights(repo_id: str) -> dict:
    with _lock:
        return REPO_INSIGHTS.get(repo_id, {})
