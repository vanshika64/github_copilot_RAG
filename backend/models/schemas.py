"""
Pydantic request/response models shared across the API.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ------------------------- Repository setup ---------------------------------

class RepoRequest(BaseModel):
    url: str
    github_token: Optional[str] = None


class RepoValidationResponse(BaseModel):
    valid: bool
    owner: Optional[str] = None
    repo: Optional[str] = None
    is_private: Optional[bool] = None
    exists: bool = False
    message: str
    default_branch: Optional[str] = None
    stars: Optional[int] = None
    language: Optional[str] = None
    description: Optional[str] = None


class IndexStartResponse(BaseModel):
    repo_id: str
    message: str


class IndexStatusResponse(BaseModel):
    repo_id: str
    stage: str
    progress: int          # 0-100
    message: str
    done: bool
    error: Optional[str] = None


class RepoStats(BaseModel):
    repo_id: str
    total_files_scanned: int
    total_files_indexed: int
    total_chunks: int
    languages: Dict[str, int]
    total_lines: int
    stars: Optional[int] = None
    description: Optional[str] = None
    default_branch: Optional[str] = None


# ------------------------- Explorer -----------------------------------------

class FileTreeResponse(BaseModel):
    repo_id: str
    tree: Dict[str, Any]


class FileContentResponse(BaseModel):
    path: str
    content: str
    language: str


class ExplainFileRequest(BaseModel):
    repo_id: str
    path: str


class ExplainFileResponse(BaseModel):
    path: str
    explanation: str


class ArchitectureResponse(BaseModel):
    repo_id: str
    overview: str


class SummaryResponse(BaseModel):
    repo_id: str
    summary: str
    key_features: List[str]
    tech_stack: List[str]


class SuggestedQuestionsResponse(BaseModel):
    repo_id: str
    questions: List[str]


# ------------------------- Chat ----------------------------------------------

class ChatMessage(BaseModel):
    role: str      # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    repo_id: str
    query: str
    history: Optional[List[ChatMessage]] = Field(default_factory=list)


class SourceChunk(BaseModel):
    file_path: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    snippet: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]
    suggested_followups: List[str] = Field(default_factory=list)
