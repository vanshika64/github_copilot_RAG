"""
Handles everything related to talking to GitHub:
 - parsing / validating a repo URL
 - checking existence & public/private status via the GitHub API
 - cloning the repository to local disk
 - fetching repo metadata (stars, language, description, default branch)
"""
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

from backend.utils.config import GITHUB_API_URL, GITHUB_TOKEN, CLONE_DIR

GITHUB_URL_RE = re.compile(
    r"^(https?://)?(www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(\.git)?/?$"
)


class GitHubServiceError(Exception):
    pass


def parse_github_url(url: str) -> Tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL. Raises GitHubServiceError if malformed."""
    url = url.strip()
    match = GITHUB_URL_RE.match(url)
    if not match:
        raise GitHubServiceError(
            "That doesn't look like a valid GitHub repository URL. "
            "Expected something like https://github.com/owner/repo"
        )
    return match.group("owner"), match.group("repo")


def _auth_headers(token: Optional[str] = None) -> dict:
    tok = token or GITHUB_TOKEN
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def validate_repo(url: str, token: Optional[str] = None) -> dict:
    """
    Validate that a repo URL is well-formed and exists (public, or private
    with a valid token). Returns a dict suitable for RepoValidationResponse.
    """
    try:
        owner, repo = parse_github_url(url)
    except GitHubServiceError as e:
        return {"valid": False, "exists": False, "message": str(e)}

    api_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
    try:
        resp = requests.get(api_url, headers=_auth_headers(token), timeout=10)
    except requests.RequestException as e:
        return {
            "valid": False, "exists": False, "owner": owner, "repo": repo,
            "message": f"Could not reach GitHub API: {e}",
        }

    if resp.status_code == 404:
        return {
            "valid": False, "exists": False, "owner": owner, "repo": repo,
            "message": "Repository not found. It may be private (add a token) "
                       "or the URL may be incorrect.",
        }
    if resp.status_code == 401 or resp.status_code == 403:
        return {
            "valid": False, "exists": False, "owner": owner, "repo": repo,
            "message": "Access denied by GitHub API (rate limited or invalid token).",
        }
    if resp.status_code != 200:
        return {
            "valid": False, "exists": False, "owner": owner, "repo": repo,
            "message": f"Unexpected GitHub API response: {resp.status_code}",
        }

    data = resp.json()
    return {
        "valid": True,
        "exists": True,
        "owner": owner,
        "repo": repo,
        "is_private": data.get("private", False),
        "default_branch": data.get("default_branch", "main"),
        "stars": data.get("stargazers_count", 0),
        "language": data.get("language"),
        "description": data.get("description") or "",
        "message": "Repository found and accessible.",
    }


def repo_id_from(owner: str, repo: str) -> str:
    return f"{owner}__{repo}".lower()


def clone_repository(url: str, owner: str, repo: str, token: Optional[str] = None) -> Path:
    """
    Shallow-clone the repository into CLONE_DIR/<repo_id>. Re-clones fresh
    each time (removes any previous copy) to avoid stale state.
    """
    repo_id = repo_id_from(owner, repo)
    dest = CLONE_DIR / repo_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    clone_url = url
    if token:
        parsed = urlparse(url)
        clone_url = f"{parsed.scheme}://{token}@{parsed.netloc}{parsed.path}"

    cmd = ["git", "clone", "--depth", "1", clone_url, str(dest)]
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=300
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(dest, ignore_errors=True)
        stderr = (e.stderr or "").replace(token or "###", "***")
        raise GitHubServiceError(f"git clone failed: {stderr}")
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise GitHubServiceError("git clone timed out after 300s")

    return dest
