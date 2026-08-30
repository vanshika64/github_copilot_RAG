"""
Central configuration for the GitHub RAG Copilot backend.
All values are loaded from environment variables (see .env).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_ROOT = BASE_DIR.parent                              # github-rag-copilot/

# ---- Storage locations -----------------------------------------------------
CLONE_DIR = Path(os.getenv("CLONE_DIR", BASE_DIR / "repo_cache"))
INDEX_DIR = Path(os.getenv("INDEX_DIR", BASE_DIR / "vectorstore" / "indexes"))
CLONE_DIR.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)

# ---- GitHub -----------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_URL = "https://api.github.com"

# ---- LLM (Groq) ---------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ---- Embeddings ---------------------------------------------------------------
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# ---- Chunking -------------------------------------------------------------
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", 1500))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", 200))
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", 500_000))  # skip huge generated files

# ---- Retrieval --------------------------------------------------------------
TOP_K_RETRIEVE = int(os.getenv("TOP_K_RETRIEVE", 20))
TOP_N_RERANK = int(os.getenv("TOP_N_RERANK", 6))

# ---- Server -----------------------------------------------------------------
BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", 8000))
BACKEND_URL = os.getenv("BACKEND_URL", f"http://localhost:{BACKEND_PORT}")
