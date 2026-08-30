"""
Parses source files into semantic "code units" (functions, classes,
methods, or config blocks) that the chunking service then turns into
retrieval chunks. Python uses the `ast` module for precise boundaries;
other languages fall back to a regex-based structural parser that looks
for common function/class declaration patterns.
"""
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class CodeUnit:
    name: str
    kind: str            # "function" | "class" | "method" | "module" | "config" | "block"
    start_line: int
    end_line: int
    code: str
    docstring: str = ""


LANGUAGE_BY_EXT = {
    ".py": "python", ".java": "java", ".cpp": "cpp", ".cc": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".kt": "kotlin",
    ".swift": "swift", ".scala": "scala", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".md": "markdown", ".rst": "markdown", ".sql": "sql", ".sh": "bash",
}


def detect_language(file_path: Path) -> str:
    return LANGUAGE_BY_EXT.get(file_path.suffix.lower(), "text")


# --------------------------- Python (AST-based) ------------------------------

def _parse_python(content: str) -> List[CodeUnit]:
    units: List[CodeUnit] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _parse_generic(content, comment_prefix="#")

    lines = content.splitlines()

    def get_source(node) -> str:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        return "\n".join(lines[start:end])

    top_level_covered = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units.append(CodeUnit(
                name=node.name, kind="function",
                start_line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                code=get_source(node), docstring=ast.get_docstring(node) or "",
            ))
            top_level_covered.add((node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node) or ""
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if methods:
                for m in methods:
                    units.append(CodeUnit(
                        name=f"{node.name}.{m.name}", kind="method",
                        start_line=m.lineno, end_line=getattr(m, "end_lineno", m.lineno),
                        code=get_source(m), docstring=ast.get_docstring(m) or "",
                    ))
            else:
                units.append(CodeUnit(
                    name=node.name, kind="class",
                    start_line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                    code=get_source(node), docstring=class_doc,
                ))
            top_level_covered.add((node.lineno, getattr(node, "end_lineno", node.lineno)))

    # Anything not captured as a function/class (imports, constants, script code)
    # gets bundled as a "module" unit so nothing is lost.
    covered_lines = set()
    for s, e in top_level_covered:
        covered_lines.update(range(s, e + 1))
    remaining = [i + 1 for i in range(len(lines)) if (i + 1) not in covered_lines and lines[i].strip()]
    if remaining:
        module_doc = ast.get_docstring(tree) or ""
        remaining_code = "\n".join(lines[i - 1] for i in remaining)
        if remaining_code.strip():
            units.append(CodeUnit(
                name="<module-level>", kind="module",
                start_line=remaining[0], end_line=remaining[-1],
                code=remaining_code, docstring=module_doc,
            ))

    if not units:
        units.append(CodeUnit(name="<file>", kind="module", start_line=1,
                               end_line=len(lines), code=content))
    return units


# --------------------------- Generic regex-based parser -----------------------

# Patterns that roughly capture function/class/method declarations across
# C-like languages (JS/TS/Java/C/C++/Go/Rust/PHP/C#/Kotlin/Swift/Scala/Ruby).
_DECL_PATTERNS = [
    # function foo(...)  |  export function foo(...) | async function foo(...)
    re.compile(r'^\s*(export\s+)?(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\('),
    # const/let/var foo = (...) => {   or   function(...)
    re.compile(r'^\s*(export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(async\s*)?\(?.*=>'),
    # class Foo
    re.compile(r'^\s*(export\s+)?(public\s+|abstract\s+|final\s+)*class\s+([A-Za-z_$][\w$]*)'),
    # interface Foo
    re.compile(r'^\s*(export\s+)?interface\s+([A-Za-z_$][\w$]*)'),
    # Java/C#/C++ style: [modifiers] returnType name(...) {
    re.compile(r'^\s*(public|private|protected|static|final|virtual|override|async)\s+[\w<>\[\],\s]+?\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?'),
    # Go: func (recv) Name(...)  or func Name(...)
    re.compile(r'^\s*func\s+(\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\('),
    # Rust: fn name(...)
    re.compile(r'^\s*(pub\s+)?(async\s+)?fn\s+([A-Za-z_]\w*)\s*\('),
    # Ruby: def name
    re.compile(r'^\s*def\s+([A-Za-z_]\w*[!?]?)'),
    # Ruby class/module
    re.compile(r'^\s*(class|module)\s+([A-Za-z_:]\w*)'),
    # PHP function
    re.compile(r'^\s*(public|private|protected|static)?\s*function\s+([A-Za-z_]\w*)\s*\('),
]


def _parse_generic(content: str, comment_prefix: str = "//") -> List[CodeUnit]:
    lines = content.splitlines()
    if not lines:
        return []

    boundaries: List[int] = []  # line indices (0-based) where a new unit starts
    names: List[str] = []

    for i, line in enumerate(lines):
        for pat in _DECL_PATTERNS:
            m = pat.match(line)
            if m:
                # last capturing group with alnum content is usually the name
                name = next((g for g in reversed(m.groups()) if g and re.match(r'^[A-Za-z_]', g)), "block")
                boundaries.append(i)
                names.append(name)
                break

    units: List[CodeUnit] = []
    if not boundaries:
        return [CodeUnit(name="<file>", kind="block", start_line=1, end_line=len(lines), code=content)]

    # Content before first declaration (imports/config header)
    if boundaries[0] > 0:
        header = "\n".join(lines[:boundaries[0]]).strip()
        if header:
            units.append(CodeUnit(name="<header>", kind="block", start_line=1,
                                   end_line=boundaries[0], code=header))

    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        code = "\n".join(lines[start:end]).rstrip()
        units.append(CodeUnit(
            name=names[idx], kind="function",
            start_line=start + 1, end_line=end, code=code,
        ))

    return units


def _parse_structured_config(content: str) -> List[CodeUnit]:
    """YAML/JSON/TOML/ini: treat the whole file as one config unit (usually small)."""
    lines = content.splitlines() or [""]
    return [CodeUnit(name="<config>", kind="config", start_line=1, end_line=len(lines), code=content)]


def _parse_markdown(content: str) -> List[CodeUnit]:
    """Split markdown by headings so each doc section becomes a unit."""
    lines = content.splitlines()
    heading_re = re.compile(r'^(#{1,6})\s+(.*)')
    boundaries = [i for i, l in enumerate(lines) if heading_re.match(l)]
    if not boundaries:
        return [CodeUnit(name="<doc>", kind="block", start_line=1, end_line=len(lines) or 1, code=content)]

    units = []
    if boundaries[0] > 0:
        header = "\n".join(lines[:boundaries[0]]).strip()
        if header:
            units.append(CodeUnit(name="<intro>", kind="block", start_line=1, end_line=boundaries[0], code=header))

    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        title_match = heading_re.match(lines[start])
        title = title_match.group(2).strip() if title_match else "<section>"
        code = "\n".join(lines[start:end]).rstrip()
        units.append(CodeUnit(name=title, kind="block", start_line=start + 1, end_line=end, code=code))
    return units


def parse_file(file_path: Path, content: str) -> List[CodeUnit]:
    """Dispatch to the right parser based on file extension."""
    lang = detect_language(file_path)
    if lang == "python":
        return _parse_python(content)
    if lang in ("yaml", "json", "toml", "ini"):
        return _parse_structured_config(content)
    if lang == "markdown":
        return _parse_markdown(content)
    return _parse_generic(content)
