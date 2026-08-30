"""
Turns the CodeUnits produced by parser_service into retrieval-ready
Chunks. Unlike a plain RecursiveCharacterTextSplitter, this respects
function/class boundaries:

 - small, related units (e.g. several short functions) are merged
   together up to MAX_CHUNK_CHARS so we don't retrieve tiny fragments
 - large units (a huge function/class) are split with overlap so no
   single chunk blows past the embedding model's comfortable context
 - every chunk keeps its originating file, unit name, kind and line
   range for accurate citations later
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from services.parser_service import parse_file, detect_language, CodeUnit
from utils.config import MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS


@dataclass
class Chunk:
    chunk_id: str
    file_path: str
    language: str
    chunk_type: str
    name: str
    start_line: int
    end_line: int
    content: str        # the text that gets embedded (includes light context header)
    code: str            # raw code, for display


def _split_large_unit(unit: CodeUnit, max_chars: int, overlap: int) -> List[CodeUnit]:
    """Sliding-window split (by lines) for a single oversized unit."""
    lines = unit.code.splitlines()
    if len(unit.code) <= max_chars or len(lines) <= 3:
        return [unit]

    # estimate avg chars/line to translate char budget -> line budget
    avg_len = max(1, len(unit.code) // max(1, len(lines)))
    lines_per_chunk = max(5, max_chars // avg_len)
    overlap_lines = max(1, overlap // avg_len)

    parts = []
    i = 0
    part_num = 1
    while i < len(lines):
        window = lines[i:i + lines_per_chunk]
        code = "\n".join(window)
        parts.append(CodeUnit(
            name=f"{unit.name} (part {part_num})",
            kind=unit.kind,
            start_line=unit.start_line + i,
            end_line=unit.start_line + i + len(window) - 1,
            code=code,
            docstring=unit.docstring if part_num == 1 else "",
        ))
        part_num += 1
        if i + lines_per_chunk >= len(lines):
            break
        i += lines_per_chunk - overlap_lines
    return parts


def _merge_small_units(units: List[CodeUnit], max_chars: int) -> List[CodeUnit]:
    """Greedily merge consecutive small units so we don't index tiny fragments."""
    merged: List[CodeUnit] = []
    buffer: List[CodeUnit] = []
    buf_len = 0

    def flush():
        nonlocal buffer, buf_len
        if not buffer:
            return
        if len(buffer) == 1:
            merged.append(buffer[0])
        else:
            names = ", ".join(u.name for u in buffer)
            code = "\n\n".join(u.code for u in buffer)
            merged.append(CodeUnit(
                name=names, kind=buffer[0].kind,
                start_line=buffer[0].start_line, end_line=buffer[-1].end_line,
                code=code,
            ))
        buffer = []
        buf_len = 0

    for u in units:
        u_len = len(u.code)
        if u_len > max_chars:
            flush()
            merged.append(u)
            continue
        if buf_len + u_len > max_chars and buffer:
            flush()
        buffer.append(u)
        buf_len += u_len
    flush()
    return merged


def chunk_file(file_path: Path, rel_path: str, content: str) -> List[Chunk]:
    language = detect_language(file_path)
    units = parse_file(file_path, content)

    # Step 1: split anything oversized
    expanded: List[CodeUnit] = []
    for u in units:
        expanded.extend(_split_large_unit(u, MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS))

    # Step 2: merge small sibling units so we don't index noise-level fragments
    final_units = _merge_small_units(expanded, MAX_CHUNK_CHARS)

    chunks: List[Chunk] = []
    for i, u in enumerate(final_units):
        header = f"# File: {rel_path}\n# {u.kind}: {u.name} (lines {u.start_line}-{u.end_line})\n"
        if u.docstring:
            header += f"# Docstring: {u.docstring.strip()[:300]}\n"
        embed_text = header + u.code
        chunks.append(Chunk(
            chunk_id=f"{rel_path}::{u.start_line}-{u.end_line}::{i}",
            file_path=rel_path,
            language=language,
            chunk_type=u.kind,
            name=u.name,
            start_line=u.start_line,
            end_line=u.end_line,
            content=embed_text,
            code=u.code,
        ))
    return chunks


def chunk_repository(files: List[Path], repo_root: Path) -> List[Chunk]:
    all_chunks: List[Chunk] = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(fp.relative_to(repo_root))
        try:
            all_chunks.extend(chunk_file(fp, rel, text))
        except Exception:
            # Never let a single bad file kill the whole indexing run
            continue
    return all_chunks
