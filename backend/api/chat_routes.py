"""
Chat endpoint: query understanding -> retrieval -> reranking ->
prompt construction -> LLM answer -> citations -> suggested follow-ups.
"""
from fastapi import APIRouter, HTTPException

from models.schemas import ChatRequest, ChatResponse, SourceChunk
from services.embedding_service import index_exists
from services.retrieval_service import retrieve_and_rerank, understand_query
from services import llm_service
from utils import state

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _answer_list_files(repo_id: str) -> ChatResponse:
    """
    Bypasses vector search entirely for 'how many files / list files'
    questions. Semantic retrieval only ever returns a handful of chunks,
    so it can't answer this kind of aggregate question correctly -- the
    exact file list already lives in utils.state from indexing, so we
    answer directly from that instead of letting the LLM guess.
    """
    meta = state.get_meta(repo_id)
    files = sorted(meta.get("files", []))
    stats = meta.get("stats", {})
    total = stats.get("total_files_indexed", len(files))

    lines = [f"This repository has **{total} indexed files**:", ""]
    lines += [f"- `{f}`" for f in files]
    answer = "\n".join(lines)

    return ChatResponse(
        answer=answer,
        sources=[],
        suggested_followups=[
            "What does the main entry point file do?",
            "Explain the architecture of this repository.",
        ],
    )


@router.post("/query", response_model=ChatResponse)
def chat_query(req: ChatRequest):
    if not index_exists(req.repo_id):
        raise HTTPException(status_code=404, detail="Repository not indexed yet. Index it first.")

    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # 1) query understanding (used for lexical boosting inside retrieval,
    #    and to route aggregate questions away from semantic search)
    qinfo = understand_query(req.query)
    if qinfo["intent"] == "list_files":
        return _answer_list_files(req.repo_id)

    # 2) retrieval + 3) reranking
    ranked = retrieve_and_rerank(req.repo_id, req.query)
    if not ranked:
        raise HTTPException(status_code=404, detail="No relevant content found for this query.")

    # 4) prompt construction + generation (with citations)
    history_text = "\n".join(f"{m.role}: {m.content}" for m in (req.history or [])[-6:])
    answer = llm_service.generate_answer(req.query, ranked, history_text)

    # 5) follow-up suggestions
    followups = llm_service.generate_followups(req.query, answer)

    sources = [
        SourceChunk(
            file_path=chunk.file_path,
            chunk_type=chunk.chunk_type,
            name=chunk.name,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            snippet=chunk.code[:400],
            score=float(score),
        )
        for chunk, score in ranked
    ]

    return ChatResponse(answer=answer, sources=sources, suggested_followups=followups)
