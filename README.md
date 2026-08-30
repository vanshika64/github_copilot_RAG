# GitHub RAG Copilot

Chat with any GitHub repository. Point it at a repo URL, it clones the repo,
parses & chunks the code in a **code-aware** way (function/class boundaries,
not blind character splitting), embeds it, indexes it in FAISS, and lets you
ask questions with cited sources — plus a repo summary dashboard and a file
explorer with AI explanations.

## Architecture

```
github-rag-copilot/
│
├── frontend/                # Streamlit UI
│   └── app.py
│
├── backend/                 # FastAPI service
│   ├── main.py               # app entrypoint
│   ├── api/
│   │   ├── repository_routes.py   # validate / index / stats / explorer / insights
│   │   └── chat_routes.py         # RAG chat endpoint
│   ├── services/
│   │   ├── github_service.py      # validate, clone, metadata
│   │   ├── parser_service.py      # AST (Python) + regex structural parsing (other langs)
│   │   ├── chunking_service.py    # code-aware semantic chunking
│   │   ├── embedding_service.py   # sentence-transformers + FAISS
│   │   ├── retrieval_service.py   # vector search + cross-encoder reranking
│   │   └── llm_service.py         # Groq LLM: answers, summaries, explanations
│   ├── models/schemas.py     # Pydantic request/response models
│   ├── vectorstore/indexes/  # persisted FAISS indexes (per repo_id)
│   └── utils/
│       ├── file_utils.py     # file filtering (skip binaries/media/caches)
│       ├── config.py         # env-driven configuration
│       └── state.py          # in-memory indexing/progress/insights store
│
├── .env
├── requirements.txt
└── README.md
```

## RAG pipeline (end to end)

1. **User enters a repo URL** in the Streamlit UI → sent to the backend.
2. **Validation** — `GET /repos/{owner}/{repo}` against the GitHub API checks
   the URL is well-formed, the repo exists, and whether it's public/private.
3. **Clone** — shallow `git clone --depth 1` into `backend/repo_cache/<repo_id>`,
   with an optional token for private repos.
4. **Filtering** — `.git/`, `node_modules/`, `__pycache__/`, images, videos,
   binaries and lockfiles are stripped out; `.py .java .cpp .js .yml .yaml
   .json` (and several more source/config extensions) are kept.
5. **Parsing** — Python files are parsed with `ast` for precise
   function/class/method boundaries + docstrings. Other languages use a
   structural regex parser (function/class/interface declarations across
   JS/TS/Java/Go/Rust/Ruby/PHP/C#/etc.); YAML/JSON/config files and Markdown
   get their own lightweight parsers.
6. **Code-aware semantic chunking** — chunks respect function/class
   boundaries instead of blind character splitting: oversized units are
   split with overlap, small sibling units are merged up to a target size,
   and every chunk keeps a header with file path / unit name / line range.
7. **Embeddings** — `sentence-transformers` (`all-MiniLM-L6-v2` by default)
   embeds every chunk.
8. **Indexing status** — a background thread updates progress
   (`cloning → filtering → parsing_chunking → embedding →
   generating_insights → done`), polled by the UI with a live progress bar.
9. **Chat interface** — Streamlit chat UI with history, streaming-style
   progressive answers, and clickable suggested questions.
10. **Query understanding** — lightweight intent + file-hint + keyword
    extraction used to boost retrieval.
11. **Retrieval** — FAISS cosine-similarity search over the chunk index
    (top-K).
12. **Reranking** — a cross-encoder (`ms-marco-MiniLM-L-6-v2`) rescoring the
    top-K candidates down to the best top-N.
13. **Prompt construction** — numbered context snippets + conversation
    history assembled into a grounded prompt.
14. **Answer generation with citations** — Groq LLM (`llama-3.3-70b-versatile`
    by default) answers using only the provided context, citing `[1] [2] ...`.
15. **Sources displayed in the UI** — file path, unit name, line range, and
    a relevance score for every citation, with the code snippet.
16. **Repository summary dashboard** — auto-generated overview, key
    features, and detected tech stack, shown before you start chatting.
17. **Explain repo features** — part of the summary dashboard.
18. **Architecture explanation** — a dedicated LLM-generated architecture
    write-up in the Explorer tab.
19. **Code explanations** — pick any file in the Explorer and get an
    AI-generated explanation of its purpose and key logic.
20. **Suggested questions** — auto-generated starter questions shown above
    the chat box.

## Setup

```bash
# 1. Clone this project and enter it
cd github-rag-copilot

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env .env.local   # or just edit .env directly
# Set GROQ_API_KEY (required) — free key at https://console.groq.com
# Set GITHUB_TOKEN (optional) — only needed for private repos / higher rate limits
```

## Running

Open two terminals.

**Terminal 1 — backend:**
```bash
cd backend
uvicorn main:app --reload --port 8000 --reload-exclude "repo_cache/*" --reload-exclude "vectorstore/*"
```

> ⚠️ **Don't run plain `uvicorn main:app --reload`.** Indexing clones repos
> into `backend/repo_cache/` and writes FAISS files into
> `backend/vectorstore/indexes/`. Without the excludes above, the
> `--reload` file watcher sees those writes as "code changes" and restarts
> the whole server mid-index — which wipes the in-memory indexing progress
> in `utils/state.py` and makes `/api/repository/status/<repo_id>` return
> 404 forever. (Equivalently, `python main.py` already has these excludes
> baked in.)

**Terminal 2 — frontend:**
```bash
cd frontend
streamlit run app.py
```

Then open the Streamlit URL (usually `http://localhost:8501`), paste a
GitHub repo URL on the **Repository Setup** page, wait for indexing to
finish, and switch to **Repository Chat** or **Repository Explorer**.

## Notes

- The embedding model (`all-MiniLM-L6-v2`) and reranker
  (`cross-encoder/ms-marco-MiniLM-L-6-v2`) download from Hugging Face on
  first use — make sure the machine running the backend has outbound
  internet access the first time you index a repo.
- Indexes persist on disk under `backend/vectorstore/indexes/<owner>__<repo>/`,
  so re-opening a previously indexed repo (same URL) is instant — no need to
  re-index unless you want to refresh with the latest commit.
- This is a single-process demo: indexing status/insights live in memory in
  `utils/state.py`. For multi-worker/production deployments, swap that for
  Redis or a database.
