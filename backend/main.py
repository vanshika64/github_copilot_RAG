"""
FastAPI application entrypoint for GitHub RAG Copilot backend.

Run with:
    uvicorn main:app --reload --port 8000
(from inside the backend/ directory, so the `api`, `services`, `models`,
`utils` packages resolve correctly)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import repository_routes, chat_routes
from utils.config import BACKEND_HOST, BACKEND_PORT

app = FastAPI(
    title="GitHub RAG Copilot API",
    description="Index any GitHub repository and chat with it using RAG.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repository_routes.router)
app.include_router(chat_routes.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "github-rag-copilot-backend"}


@app.get("/health")
def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    # IMPORTANT: exclude the runtime data directories (repo_cache/, vectorstore/)
    # from the reload watcher. Indexing writes hundreds of files into
    # repo_cache/ while cloning; without these excludes, WatchFiles treats
    # that as a code change and restarts the whole process mid-index,
    # wiping the in-memory indexing status/progress (utils/state.py) and
    # making status polling 404 forever.
    uvicorn.run(
        "main:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
        reload=True,
        reload_excludes=["repo_cache/*", "vectorstore/*", "*.faiss", "*.pkl"],
    )
