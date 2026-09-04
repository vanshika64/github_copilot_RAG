"""
Endpoints for:
 - validating a GitHub URL
 - kicking off indexing (clone -> filter -> parse -> chunk -> embed -> FAISS)
 - polling indexing progress
 - repository stats / file tree / file content
 - repo summary dashboard, architecture overview, suggested questions
 - explaining a single file
"""
import threading
import time
from pathlib import Path
from collections import Counter

from fastapi import APIRouter, HTTPException, BackgroundTasks

from backend.models.schemas import (
    RepoRequest, RepoValidationResponse, IndexStartResponse, IndexStatusResponse,
    RepoStats, FileTreeResponse, FileContentResponse, ExplainFileRequest,
    ExplainFileResponse, ArchitectureResponse, SummaryResponse, SuggestedQuestionsResponse,
)
from backend.services import github_service
from backend.services.parser_service import detect_language
from backend.services.chunking_service import chunk_repository
from backend.services.embedding_service import build_and_save_index, save_stats, load_stats, index_exists
from backend.services import llm_service
from backend.utils.file_utils import filter_files, build_file_tree
from backend.utils import state

router = APIRouter(prefix="/api/repository", tags=["repository"])


@router.post("/validate", response_model=RepoValidationResponse)
def validate(req: RepoRequest):
    result = github_service.validate_repo(req.url, req.github_token)
    return RepoValidationResponse(**result)


def _run_indexing_pipeline(repo_id: str, url: str, owner: str, repo: str, token: str):
    try:
        state.set_status(repo_id, "cloning", 5, "Cloning repository...")
        repo_root = github_service.clone_repository(url, owner, repo, token)
        state.set_meta(repo_id, repo_root=str(repo_root), owner=owner, repo=repo, url=url)

        state.set_status(repo_id, "filtering", 20, "Filtering relevant source files...")
        files = filter_files(repo_root)
        if not files:
            raise ValueError("No indexable source files were found in this repository.")

        state.set_status(repo_id, "parsing_chunking", 40, f"Parsing & chunking {len(files)} files...")
        chunks = chunk_repository(files, repo_root)
        if not chunks:
            raise ValueError("Parsing produced no chunks.")

        state.set_status(repo_id, "embedding", 65, f"Generating embeddings for {len(chunks)} chunks...")
        build_and_save_index(repo_id, chunks)

        # ---- stats ----
        lang_counter = Counter(detect_language(f) for f in files)
        total_lines = 0
        for f in files:
            try:
                total_lines += sum(1 for _ in open(f, "r", encoding="utf-8", errors="ignore"))
            except Exception:
                pass

        stats = {
            "repo_id": repo_id,
            "total_files_scanned": len(files),
            "total_files_indexed": len(files),
            "total_chunks": len(chunks),
            "languages": dict(lang_counter),
            "total_lines": total_lines,
        }
        save_stats(repo_id, stats)
        state.set_meta(repo_id, files=[str(f.relative_to(repo_root)) for f in files], stats=stats)

        state.set_status(repo_id, "generating_insights", 85, "Generating repo summary & insights...")
        _generate_insights(repo_id)

        state.set_status(repo_id, "done", 100, "Indexing complete!", done=True)
    except Exception as e:
        state.set_status(repo_id, "error", 0, "Indexing failed.", done=True, error=str(e))


def _sample_snippets_for_llm(repo_id: str, max_files: int = 8) -> str:
    meta = state.get_meta(repo_id)
    repo_root = Path(meta.get("repo_root", ""))
    files = meta.get("files", [])[:max_files]
    parts = []
    for rel in files:
        fp = repo_root / rel
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")[:800]
            parts.append(f"--- {rel} ---\n{content}")
        except Exception:
            continue
    return "\n\n".join(parts)


def _file_tree_str(repo_id: str, max_entries: int = 200) -> str:
    meta = state.get_meta(repo_id)
    files = meta.get("files", [])[:max_entries]
    return "\n".join(files)


def _generate_insights(repo_id: str):
    meta = state.get_meta(repo_id)
    tree_str = _file_tree_str(repo_id)
    snippets = _sample_snippets_for_llm(repo_id)
    repo_meta_for_llm = {
        "owner": meta.get("owner"), "repo": meta.get("repo"),
        "stats": meta.get("stats", {}),
    }
    try:
        summary = llm_service.generate_repo_summary(repo_meta_for_llm, tree_str, snippets)
    except Exception as e:
        summary = {"summary": f"Could not generate summary: {e}", "key_features": [], "tech_stack": []}
    try:
        questions = llm_service.generate_suggested_questions(summary)
    except Exception:
        questions = []
    try:
        architecture = llm_service.generate_architecture_overview(tree_str, snippets)
    except Exception as e:
        architecture = f"Could not generate architecture overview: {e}"

    state.set_insights(
        repo_id,
        summary=summary.get("summary", ""),
        key_features=summary.get("key_features", []),
        tech_stack=summary.get("tech_stack", []),
        suggested_questions=questions,
        architecture=architecture,
    )


@router.post("/index", response_model=IndexStartResponse)
def start_indexing(req: RepoRequest, background_tasks: BackgroundTasks):
    validation = github_service.validate_repo(req.url, req.github_token)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=validation["message"])

    owner, repo = validation["owner"], validation["repo"]
    repo_id = github_service.repo_id_from(owner, repo)

    state.set_status(repo_id, "queued", 0, "Queued for indexing...")
    state.set_meta(
        repo_id, stars=validation.get("stars"), language=validation.get("language"),
        description=validation.get("description"), default_branch=validation.get("default_branch"),
        is_private=validation.get("is_private"),
    )

    thread = threading.Thread(
        target=_run_indexing_pipeline,
        args=(repo_id, req.url, owner, repo, req.github_token),
        daemon=True,
    )
    thread.start()

    return IndexStartResponse(repo_id=repo_id, message="Indexing started.")


@router.get("/status/{repo_id}", response_model=IndexStatusResponse)
def get_status(repo_id: str):
    status = state.get_status(repo_id)
    if not status:
        raise HTTPException(status_code=404, detail="Unknown repo_id. Start indexing first.")
    return IndexStatusResponse(**status)


@router.get("/stats/{repo_id}", response_model=RepoStats)
def get_stats(repo_id: str):
    if not index_exists(repo_id):
        raise HTTPException(status_code=404, detail="Repository not indexed yet.")
    stats = load_stats(repo_id)
    meta = state.get_meta(repo_id)
    return RepoStats(
        repo_id=repo_id,
        total_files_scanned=stats.get("total_files_scanned", 0),
        total_files_indexed=stats.get("total_files_indexed", 0),
        total_chunks=stats.get("total_chunks", 0),
        languages=stats.get("languages", {}),
        total_lines=stats.get("total_lines", 0),
        stars=meta.get("stars"),
        description=meta.get("description"),
        default_branch=meta.get("default_branch"),
    )


@router.get("/files/{repo_id}", response_model=FileTreeResponse)
def get_file_tree(repo_id: str):
    meta = state.get_meta(repo_id)
    repo_root = meta.get("repo_root")
    if not repo_root:
        raise HTTPException(status_code=404, detail="Repository not indexed yet.")
    files = [Path(repo_root) / f for f in meta.get("files", [])]
    tree = build_file_tree(Path(repo_root), files)
    return FileTreeResponse(repo_id=repo_id, tree=tree)


@router.get("/file-content/{repo_id}", response_model=FileContentResponse)
def get_file_content(repo_id: str, path: str):
    meta = state.get_meta(repo_id)
    repo_root = meta.get("repo_root")
    if not repo_root:
        raise HTTPException(status_code=404, detail="Repository not indexed yet.")
    full_path = (Path(repo_root) / path).resolve()
    if not str(full_path).startswith(str(Path(repo_root).resolve())):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    content = full_path.read_text(encoding="utf-8", errors="ignore")
    return FileContentResponse(path=path, content=content, language=detect_language(full_path))


@router.post("/explain-file", response_model=ExplainFileResponse)
def explain_file_route(req: ExplainFileRequest):
    meta = state.get_meta(req.repo_id)
    repo_root = meta.get("repo_root")
    if not repo_root:
        raise HTTPException(status_code=404, detail="Repository not indexed yet.")
    full_path = (Path(repo_root) / req.path).resolve()
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    content = full_path.read_text(encoding="utf-8", errors="ignore")
    explanation = llm_service.explain_file(req.path, content)
    return ExplainFileResponse(path=req.path, explanation=explanation)


@router.get("/summary/{repo_id}", response_model=SummaryResponse)
def get_summary(repo_id: str):
    insights = state.get_insights(repo_id)
    if not insights:
        raise HTTPException(status_code=404, detail="Insights not available yet.")
    return SummaryResponse(
        repo_id=repo_id,
        summary=insights.get("summary", ""),
        key_features=insights.get("key_features", []),
        tech_stack=insights.get("tech_stack", []),
    )


@router.get("/architecture/{repo_id}", response_model=ArchitectureResponse)
def get_architecture(repo_id: str):
    insights = state.get_insights(repo_id)
    if not insights:
        raise HTTPException(status_code=404, detail="Insights not available yet.")
    return ArchitectureResponse(repo_id=repo_id, overview=insights.get("architecture", ""))


@router.get("/suggested-questions/{repo_id}", response_model=SuggestedQuestionsResponse)
def get_suggested_questions(repo_id: str):
    insights = state.get_insights(repo_id)
    if not insights:
        raise HTTPException(status_code=404, detail="Insights not available yet.")
    return SuggestedQuestionsResponse(repo_id=repo_id, questions=insights.get("suggested_questions", []))
