"""
All LLM calls go through this module. Uses Groq's OpenAI-compatible chat
completion API. Responsible for:
 - prompt construction for RAG answers (with inline citations)
 - repository summary / feature dashboard
 - architecture explanation
 - single-file explanation
 - suggested question generation
"""
import json
from typing import List, Tuple

from groq import Groq

from backend.utils.config import GROQ_API_KEY, GROQ_MODEL
from backend.services.chunking_service import Chunk

_client = None


def get_client() -> Groq:
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _chat(system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500) -> str:
    client = get_client()
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# --------------------------- RAG answer with citations -------------------------

RAG_SYSTEM_PROMPT = """You are a senior software engineer acting as a code-repository copilot.
You answer questions about a specific GitHub repository using ONLY the provided code context.
Rules:
- Cite the source of every factual claim using bracket notation like [1], [2] that refer
  to the numbered context snippets you were given.
- If the context does not contain enough information to answer, say so honestly instead
  of guessing.
- Be concise but complete. Prefer bullet points for multi-part answers.
- When referring to code, use inline backticks for identifiers.
"""


def build_context_block(ranked_chunks: List[Tuple[Chunk, float]]) -> str:
    parts = []
    for i, (chunk, score) in enumerate(ranked_chunks, start=1):
        parts.append(
            f"[{i}] File: {chunk.file_path} | {chunk.chunk_type}: {chunk.name} "
            f"(lines {chunk.start_line}-{chunk.end_line})\n```\n{chunk.code[:1200]}\n```"
        )
    return "\n\n".join(parts)


def generate_answer(query: str, ranked_chunks: List[Tuple[Chunk, float]], history_text: str = "") -> str:
    context = build_context_block(ranked_chunks)
    user_prompt = (
        (f"Conversation so far:\n{history_text}\n\n" if history_text else "")
        + f"Context snippets from the repository:\n\n{context}\n\n"
        f"Question: {query}\n\n"
        "Answer the question using the numbered context above and cite sources like [1]."
    )
    return _chat(RAG_SYSTEM_PROMPT, user_prompt, temperature=0.2, max_tokens=1200)


def generate_followups(query: str, answer: str) -> List[str]:
    system = "You suggest short, relevant follow-up questions a developer might ask next about a codebase."
    user = (
        f"Original question: {query}\nAnswer given: {answer}\n\n"
        "Suggest 3 short, specific follow-up questions. "
        'Respond ONLY as a JSON array of strings, e.g. ["question 1", "question 2", "question 3"]'
    )
    try:
        raw = _chat(system, user, temperature=0.4, max_tokens=200)
        raw = raw.strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)[:3]
    except Exception:
        return []


# --------------------------- Repository summary dashboard ----------------------

def generate_repo_summary(repo_meta: dict, file_tree_str: str, sample_snippets: str) -> dict:
    system = (
        "You are an expert software architect. Given metadata, a file tree, and sample code "
        "snippets from a GitHub repository, produce a concise onboarding summary for a new "
        "developer. Respond ONLY with valid JSON with keys: "
        '"summary" (2-4 sentence paragraph), "key_features" (list of 4-8 short bullet strings), '
        '"tech_stack" (list of technology/framework names detected).'
    )
    user = (
        f"Repo metadata: {json.dumps(repo_meta)}\n\n"
        f"File tree (partial):\n{file_tree_str}\n\n"
        f"Sample code snippets:\n{sample_snippets}\n\n"
        "Produce the JSON now."
    )
    raw = _chat(system, user, temperature=0.3, max_tokens=900)
    raw = raw.strip().strip("`")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = {"summary": raw, "key_features": [], "tech_stack": []}
    return data


def generate_suggested_questions(repo_summary: dict) -> List[str]:
    system = (
        "You generate a starter list of good exploratory questions a developer could ask "
        "a RAG chatbot about this repository. Respond ONLY as a JSON array of 6 short strings."
    )
    user = f"Repository summary: {json.dumps(repo_summary)}\n\nGenerate 6 suggested questions."
    try:
        raw = _chat(system, user, temperature=0.5, max_tokens=300)
        raw = raw.strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)[:6]
    except Exception:
        return [
            "What does this repository do?",
            "What is the overall architecture?",
            "What are the main entry points?",
            "How is data persisted?",
            "What external dependencies are used?",
            "How would I add a new feature here?",
        ]


# --------------------------- Architecture explanation ---------------------------

def generate_architecture_overview(file_tree_str: str, sample_snippets: str) -> str:
    system = (
        "You are a software architect explaining a codebase's architecture to a new "
        "engineer. Describe layers/modules, how they interact, and key design patterns. "
        "Use markdown with short headings and bullet points."
    )
    user = f"File tree:\n{file_tree_str}\n\nRepresentative code snippets:\n{sample_snippets}\n\nExplain the architecture."
    return _chat(system, user, temperature=0.3, max_tokens=1200)


# --------------------------- Single file explanation -----------------------------

def explain_file(path: str, content: str) -> str:
    system = (
        "You explain a single source file to a developer new to the codebase: its purpose, "
        "key functions/classes, notable logic, and how it likely fits into the larger project. "
        "Use markdown with short sections."
    )
    user = f"File: {path}\n\n```\n{content[:6000]}\n```\n\nExplain this file."
    return _chat(system, user, temperature=0.3, max_tokens=900)
