"""
Utilities to walk a cloned repository and keep only the files that are
useful for RAG indexing (source code / config), discarding binaries,
media, caches and VCS internals.
"""
from pathlib import Path
from typing import List

from utils.config import MAX_FILE_SIZE_BYTES

# Extensions we want to index (source code + important config/docs)
ALLOWED_EXTENSIONS = {
    ".py", ".java", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".rb", ".php", ".cs", ".kt", ".swift", ".scala",
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
    ".md", ".rst", ".sql", ".sh",
}

# Directories that should never be indexed
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "images", "image", "videos", "video", "assets", "static",
    "migrations", ".next", ".cache", "target", "bin", "obj",
    "coverage", ".tox", "vendor", "site-packages",
}

# Explicit binary / media extensions to always skip, even if not caught above
EXCLUDED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar", ".pyc",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".lock",
}

EXCLUDED_FILENAMES = {
    "package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock",
}


def _is_excluded_dir(path_parts) -> bool:
    return any(part in EXCLUDED_DIRS or part.startswith(".") and part not in {".", ".."}
               for part in path_parts)


def is_probably_binary(file_path: Path, sample_size: int = 2048) -> bool:
    """Heuristic binary sniff: look for NUL bytes in the first chunk."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(sample_size)
        return b"\x00" in chunk
    except Exception:
        return True


def filter_files(repo_root: Path) -> List[Path]:
    """
    Walk repo_root and return a list of absolute Paths to files that
    should be parsed & indexed.
    """
    kept: List[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue

        rel_parts = path.relative_to(repo_root).parts
        if _is_excluded_dir(rel_parts[:-1]):
            continue

        if path.name in EXCLUDED_FILENAMES:
            continue

        ext = path.suffix.lower()
        if ext in EXCLUDED_EXTENSIONS:
            continue
        if ext not in ALLOWED_EXTENSIONS:
            continue

        try:
            if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                continue
            if path.stat().st_size == 0:
                continue
        except OSError:
            continue

        if is_probably_binary(path):
            continue

        kept.append(path)

    return kept


def build_file_tree(repo_root: Path, files: List[Path]) -> dict:
    """Build a nested dict representing the directory tree of the kept files."""
    tree: dict = {}
    for f in files:
        rel = f.relative_to(repo_root)
        node = tree
        parts = rel.parts
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1])
    return tree
