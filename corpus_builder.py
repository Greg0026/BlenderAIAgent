"""bpy snippet collection pipeline for the 3Didea pipeline VectorDB.

Collects Blender Python API code snippets from multiple sources:
  1. Blender Stack Exchange (official StackExchange v2.3 API)
  2. GitHub -- curated repos (PyGitHub, hand-picked list)
  3. GitHub -- code search (PyGitHub, dynamic search across all GitHub)
  4. Blender API Docs (html from docs.blender.org with automatic page discovery)
  5. Blender Artists Forum (Discourse API)
  6. Blender Official Examples (GitHub repo blender/blender)

Output: corpus.jsonl -- one JSON per line, compatible with vectordb.py format.

Used by:
  - build_corpus() called directly or via CLI
  - load_corpus_into_vectordb() to load the corpus into Chroma VectorDB

Dependencies:
  pip install requests beautifulsoup4 PyGitHub python-dotenv

Authentication required (strongly recommended):
  GITHUB_TOKEN=ghp_xxxxxxxxxxxx    (via .env or env var)
  STACKEXCHANGE_KEY=xxxxxxxxxxxx    (via .env or env var, free)
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import requests
from bs4 import BeautifulSoup

try:
    from github import Github, GithubException, Auth
    try:
        from github import RateLimitExceededException
    except ImportError:
        from github.GithubException import RateLimitExceededException
    _GITHUB_OK = True
except ImportError:
    _GITHUB_OK = False


log = logging.getLogger("corpus_builder")


def _setup_logging(verbose: bool = False) -> None:
    """Configures basic logging for the collection pipeline.

    Args:
        verbose: If True, uses DEBUG level instead of INFO.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Loads variables from a local .env file (never versioned) if present.

    Tries python-dotenv first (if installed), then manual fallback.
    Used to read GITHUB_TOKEN, STACKEXCHANGE_KEY, etc.

    Args:
        path: Path to the .env file.
    """
    try:
        from dotenv import load_dotenv as _ld
        _ld(dotenv_path=path)
        return
    except ImportError:
        pass
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


CORPUS_PATH = Path("corpus.jsonl")

QUOTA = {
    "blender_docs":       500,
    "blender_examples":  3000,
    "blender_artists":   2000,
    "stackexchange":     6000,
    "github_repos":      3000,
    "github_search":     5500,
}
TARGET_TOTAL = sum(QUOTA.values())

MIN_CODE_LINES = 5
MAX_CODE_LINES = 500
MIN_BPY_CALLS  = 1
MIN_SCORE_SE   = 1
MAX_ITEMS_PER_REPO = 800
MAX_SECONDS_PER_REPO = 120

SE_TAGS = [
    "bpy", "blender-python", "blender-scripting",
    "blender-modifiers", "blender-geometry-nodes",
]

GITHUB_REPOS = [
    "blender/blender-addons",
    "nortikin/sverchok",
    "varkenvarken/blenderaddons",
    "elfnor/blender-scripts",
    "JacquesLucke/blender_vscode",
    "CGCookie/blender-addon-maker",
    "Radivarig/UvSquares",
    "mrachinskiy/blenchmark",
    "uhlik/bpainter",
    "granma19/blender-sculpt-tools",
    "njanakiev/blender-scripting",
    "CGCookie/retopoflow",
    "armory3d/armory",
    "sobotka/blender-luts",
    "zengleei/blender-addon",
    "eliemichel/MesoGen",
    "blender-addons-contrib/blender-addons-contrib",
    "gd-codes/moongeon",
    "Pullusb/GP_Trim_Strokes",
    "johnzero7/XNALaraMesh",
    "tin2tin/text_to_video",
    "amb/blender_io_mesh_bsp",
]

GITHUB_EXAMPLE_REPOS = [
    ("blender/blender", ["scripts/templates_py", "scripts/templates_osl"]),
    ("blender/blender-addons", ["io_scene_fbx", "io_mesh_stl", "object_print3d_toolbox",
                                 "mesh_tools", "add_mesh_extra_objects", "add_curve_extra_objects"]),
]

GITHUB_SEARCH_QUERIES = [
    "bpy.ops language:Python",
    "bpy.context.scene language:Python",
    "bpy.types.Operator language:Python",
    "bmesh.from_edit_mesh language:Python",
    "bpy.data.objects language:Python",
    "bpy.app.handlers language:Python",
    "bpy.ops.mesh.primitive language:Python",
    "bmesh.ops.create language:Python",
    "bpy.ops.object.modifier_add language:Python",
    "bpy.data.meshes.new language:Python",
    "bl_info blender addon language:Python",
    "bpy.props.FloatProperty language:Python",
    "bpy.types.Panel bl_space_type language:Python",
]

BLENDER_ARTISTS_FORUM_URLS = [
    "https://blenderartists.org/c/coding/python-support/5",
    "https://blenderartists.org/c/coding/released-scripts-and-themes/10",
    "https://blenderartists.org/c/coding/add-on-releases/79",
]

BLENDER_ARTISTS_KNOWN_THREADS = [
    "https://blenderartists.org/t/addon-development-tips-and-tricks/1175752",
    "https://blenderartists.org/t/mesh-creation-bpy-examples/",
]

BLENDER_DOCS_HUB_URLS = [
    "https://docs.blender.org/api/current/bpy.types.html",
    "https://docs.blender.org/api/current/bpy.ops.html",
    "https://docs.blender.org/api/current/bpy.html",
    "https://docs.blender.org/api/current/bmesh.html",
    "https://docs.blender.org/api/current/bmesh.ops.html",
    "https://docs.blender.org/api/current/mathutils.html",
    "https://docs.blender.org/api/current/gpu.html",
]

BLENDER_DOCS_URLS = [
    "https://docs.blender.org/api/current/info_quickstart.html",
    "https://docs.blender.org/api/current/info_overview.html",
    "https://docs.blender.org/api/current/info_tutorial_addon.html",
    "https://docs.blender.org/api/current/info_tips_and_tricks.html",
    "https://docs.blender.org/api/current/info_gotcha.html",
    "https://docs.blender.org/api/current/info_best_practice.html",
    "https://docs.blender.org/api/current/info_advanced_addon_tutorial.html",
    "https://docs.blender.org/api/current/bmesh.html",
    "https://docs.blender.org/api/current/bpy.types.Object.html",
    "https://docs.blender.org/api/current/bpy.types.Mesh.html",
    "https://docs.blender.org/api/current/bpy.types.Curve.html",
    "https://docs.blender.org/api/current/bpy.types.Material.html",
    "https://docs.blender.org/api/current/bpy.types.Context.html",
    "https://docs.blender.org/api/current/bpy.types.Operator.html",
    "https://docs.blender.org/api/current/bpy.types.Panel.html",
]

COLLECTION_MAP = {
    "boolean":      "official_docs",
    "modifier":     "official_docs",
    "export":       "official_docs",
    "import":       "official_docs",
    "manifold":     "error_patterns",
    "non_manifold": "error_patterns",
    "error":        "error_patterns",
    "repair":       "error_patterns",
    "fix":          "error_patterns",
    "weld":         "error_patterns",
    "bmesh":        "community_code",
    "curve":        "community_code",
    "shader":       "community_code",
    "material":     "community_code",
    "animation":    "community_code",
    "addon":        "community_code",
}


@dataclass
class Snippet:
    """Represents a single collected bpy code snippet.

    Attributes:
        id: Unique identifier based on content hash.
        collection: "official_docs", "community_code", or "error_patterns".
        description: Textual description of the snippet.
        code: Python code with bpy calls.
        tags: List of tags for semantic retrieval.
        source: Original source (stackexchange, github, blender_docs, etc.).
        source_url: URL of the original source.
        score: Reliability score (GitHub stars, SE score, or 100 for docs).
    """
    id: str
    collection: str
    description: str
    code: str
    tags: list[str]
    source: str
    source_url: str = ""
    score: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Snippet":
        return Snippet(**d)


def _request_with_retry(
    session: requests.Session, method: str, url: str, max_retries: int = 5, **kwargs
) -> requests.Response:
    """Executes an HTTP request with retry and exponential backoff.

    Retries on network errors, HTTP 429 (rate limit) and 5xx, with
    exponential backoff and respect for the Retry-After header.

    Args:
        session: Reusable requests session.
        method: HTTP method (GET, POST, etc.).
        url: Request URL.
        max_retries: Maximum number of attempts.
        **kwargs: Additional arguments for session.request().

    Returns:
        Requests Response if the request succeeds.

    Raises:
        Last encountered exception if all attempts fail.
    """
    kwargs.setdefault("timeout", 15)
    backoff = 2.0
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            r = session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt == max_retries:
                raise
            log.warning(f"[HTTP] Network error on {url}: {e} -- retry in {backoff:.0f}s ({attempt}/{max_retries})")
            time.sleep(backoff)
            backoff *= 2
            continue

        if r.status_code == 429 or r.status_code >= 500:
            if attempt == max_retries:
                r.raise_for_status()
            retry_after = r.headers.get("Retry-After")
            ra_seconds = float(retry_after) if retry_after else 0.0
            wait = max(ra_seconds, backoff)
            log.warning(f"[HTTP] {r.status_code} on {url} -- retry in {wait:.0f}s ({attempt}/{max_retries})")
            time.sleep(wait)
            backoff *= 2
            continue

        return r

    raise last_exc or RuntimeError(f"retries exhausted for {url}")


def _gh_call(gh: "Github", func, *args, max_retries: int = 5, **kwargs):
    """Executes a PyGitHub call with automatic rate limit handling.

    If the GitHub quota is exhausted, waits until the reset (max 15 min)
    instead of failing silently. Also retries on 403/429.

    Args:
        gh: Authenticated Github instance.
        func: PyGitHub function to call.
        *args: Positional args for func.
        max_retries: Maximum number of attempts.
        **kwargs: Keyword args for func.

    Returns:
        Result of the PyGitHub call.

    Raises:
        Last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except RateLimitExceededException as e:
            last_exc = e
            try:
                reset = gh.get_rate_limit().core.reset
                wait = max((reset - datetime.now(timezone.utc)).total_seconds(), 1) + 5
            except Exception:
                wait = 60
            wait = min(wait, 900)
            log.warning(f"[GH] Rate limit reached, waiting {wait:.0f}s (attempt {attempt}/{max_retries})")
            time.sleep(wait)
        except GithubException as e:
            last_exc = e
            if getattr(e, "status", None) in (403, 429) and attempt < max_retries:
                wait = min(2 ** attempt * 5, 120)
                log.warning(f"[GH] HTTP error {e.status}, retry in {wait}s ({attempt}/{max_retries}): {e}")
                time.sleep(wait)
            else:
                raise
    raise last_exc or RuntimeError("GitHub API call failed after retries")


def _extract_bpy_functions(code: str) -> list[str]:
    """Splits a long file into functions/methods containing bpy calls.

    Extracts only top-level definitions and class methods (not ast.walk
    over the entire tree) to avoid duplicate chunks like a full class
    + each of its methods separately.

    Args:
        code: Source Python code.

    Returns:
        List of code chunks satisfying MIN_CODE_LINES and MAX_CODE_LINES.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [code] if _has_bpy(code) else []

    chunks: list[str] = []

    def _consider(node: ast.AST) -> None:
        try:
            src = ast.get_source_segment(code, node)
        except Exception:
            return
        if src and _has_bpy(src) and MIN_CODE_LINES <= len(src.splitlines()) <= MAX_CODE_LINES:
            chunks.append(src)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _consider(node)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _consider(child)

    if not chunks and _has_bpy(code):
        lines = code.strip().splitlines()
        if MIN_CODE_LINES <= len(lines) <= MAX_CODE_LINES:
            chunks.append(code.strip())

    return chunks


def _has_bpy(code: str) -> bool:
    """Checks if the code contains at least MIN_BPY_CALLS bpy calls.

    Args:
        code: Python code to check.

    Returns:
        True if the code has sufficient bpy. calls.
    """
    return bool(re.search(r'\bbpy\.', code)) and code.count("bpy.") >= MIN_BPY_CALLS


def _clean_code(code: str) -> str:
    """Removes HTML artifacts and normalizes code indentation.

    Args:
        code: Raw code (potentially with HTML entities).

    Returns:
        Cleaned and normalized code.
    """
    code = code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    code = code.replace("&#39;", "'").replace("&quot;", '"')
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = [l.rstrip() for l in code.splitlines()]
    cleaned, blank_count = [], 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _infer_collection(text: str) -> str:
    """Infers the VectorDB collection from text (description + code).

    Args:
        text: Combined description + code text.

    Returns:
        "official_docs", "error_patterns", or "community_code" (default).
    """
    text_lower = text.lower()
    for keyword, collection in COLLECTION_MAP.items():
        if keyword in text_lower:
            return collection
    return "community_code"


def _make_id(source: str, text: str) -> str:
    """Generates a stable id based on MD5 hash of the content.

    Args:
        source: Source name (e.g. "se", "gh", "docs").
        text: Content to generate the hash from.

    Returns:
        Id string in format "<source>_<hash10>".
    """
    h = hashlib.md5(text.encode()).hexdigest()[:10]
    return f"{source}_{h}"


class _SEQuotaExhausted(RuntimeError):
    """Stack Exchange daily quota exhausted (error_id in API response).

    Distinct from a generic RuntimeError to stop collection on ALL
    remaining tags immediately (the quota is global per IP/key,
    not per tag: continuing to try is futile).
    """
    pass


def _extract_tags(code: str, description: str) -> list[str]:
    """Extracts relevant tags from snippet code and description.

    Combines predefined keywords and bpy call names found in the code.

    Args:
        code: Python code of the snippet.
        description: Textual description.

    Returns:
        Sorted list of tags (max 10).
    """
    tags = set()
    text = (code + " " + description).lower()

    keywords = [
        "bmesh", "boolean", "modifier", "solidify", "export", "stl",
        "manifold", "watertight", "normals", "weld", "duplicate", "cylinder",
        "mesh", "object", "scene", "material", "shader", "curve", "animation",
        "remesh", "subdivide", "transform", "apply", "import", "select",
        "edit_mode", "object_mode", "vertex", "edge", "face", "loop",
    ]
    for kw in keywords:
        if kw in text:
            tags.add(kw)

    bpy_calls = re.findall(r'bpy\.\w+\.\w+', code)
    for call in bpy_calls[:5]:
        tags.add(call.replace("bpy.", "").replace(".", "_"))

    return sorted(tags)[:10]


class StackExchangeCollector:
    """Collector for snippets from Blender Stack Exchange via API v2.3.

    Retrieves accepted answers or those with score > MIN_SCORE_SE that contain
    Python code blocks with bpy.

    Without API key: 300 requests/day, shared per IP among all
    unregistered apps.
    With free API key: 10,000 requests/day.

    Args:
        quota: Maximum number of snippets to collect.
        api_key: Optional Stack Exchange API key.
    """

    BASE_URL = "https://api.stackexchange.com/2.3"
    SITE = "blender"
    DELAY = 0.5

    def __init__(self, quota: int = 200, api_key: str | None = None) -> None:
        self.quota = quota
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers["Accept-Encoding"] = "gzip"
        if api_key:
            log.info("[SE] API key present (10,000 requests/day limit)")
        else:
            log.warning("[SE] No API key: 300 requests/day shared per IP limit.")

    def _get(self, endpoint: str, params: dict) -> dict:
        """Executes a GET to the Stack Exchange API.

        Args:
            endpoint: API path (e.g. /questions).
            params: Query string parameters.

        Returns:
            Parsed JSON response.

        Raises:
            _SEQuotaExhausted if the daily quota is exhausted.
        """
        params["site"] = self.SITE
        if self.api_key:
            params["key"] = self.api_key
        time.sleep(self.DELAY)
        r = _request_with_retry(self.session, "GET", f"{self.BASE_URL}{endpoint}", params=params)
        r.raise_for_status()
        data = r.json()

        if "error_id" in data:
            raise _SEQuotaExhausted(
                f"[SE] API error (error_id={data.get('error_id')}): "
                f"{data.get('error_name')} -- {data.get('error_message')}"
            )

        if data.get("quota_remaining", 999) < 10:
            log.warning(f"[SE] API quota nearly exhausted ({data.get('quota_remaining')} requests remaining)")
        backoff = data.get("backoff")
        if backoff:
            log.info(f"[SE] API requests to slow down, waiting {backoff}s extra")
            time.sleep(backoff + 1)
        return data

    def _extract_code_blocks(self, html_body: str) -> list[str]:
        """Extracts <code> blocks from Stack Exchange HTML.

        Args:
            html_body: HTML body of the SE answer.

        Returns:
            List of cleaned code blocks with bpy calls.
        """
        soup = BeautifulSoup(html_body, "html.parser")
        blocks = []
        for tag in soup.find_all(["pre", "code"]):
            text = tag.get_text()
            if _has_bpy(text):
                cleaned = _clean_code(text)
                if cleaned:
                    blocks.append(cleaned)
        return blocks

    def collect(self) -> list[Snippet]:
        """Performs collection from Stack Exchange for all configured tags.

        Returns:
            List of collected snippets (already deduplicated by hash).
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()
        quota_hit = False

        for tag in SE_TAGS:
            if quota_hit or len(snippets) >= self.quota:
                break

            page = 1
            while len(snippets) < self.quota:
                log.info(f"[SE] Tag '{tag}' page {page} ({len(snippets)}/{self.quota})")

                try:
                    data = self._get("/questions", {
                        "tagged":   tag,
                        "sort":     "votes",
                        "order":    "desc",
                        "pagesize": 50,
                        "page":     page,
                    })
                except _SEQuotaExhausted as e:
                    log.error(f"[SE] Daily quota exhausted, stopping: {e}")
                    quota_hit = True
                    break
                except Exception as e:
                    log.warning(f"[SE] Error requesting questions: {e}")
                    break

                questions = data.get("items", [])
                if not questions:
                    break

                question_ids = [str(q["question_id"]) for q in questions]

                try:
                    answers_data = self._get(
                        f"/questions/{';'.join(question_ids)}/answers",
                        {
                            "sort":     "votes",
                            "order":    "desc",
                            "pagesize": 100,
                            "filter":   "withbody",
                        },
                    )
                except _SEQuotaExhausted as e:
                    log.error(f"[SE] Daily quota exhausted, stopping: {e}")
                    quota_hit = True
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 400:
                        log.warning(f"[SE] filter=withbody rejected, retrying without filter: {e}")
                        try:
                            answers_data = self._get(
                                f"/questions/{';'.join(question_ids)}/answers",
                                {"sort": "votes", "order": "desc", "pagesize": 100},
                            )
                        except _SEQuotaExhausted as e2:
                            log.error(f"[SE] Quota exhausted: {e2}")
                            quota_hit = True
                            break
                        except Exception as e2:
                            log.warning(f"[SE] Error requesting answers (fallback): {e2}")
                            break
                    else:
                        raise

                if quota_hit:
                    break

                q_map = {str(q["question_id"]): q.get("title", "") for q in questions}

                for answer in answers_data.get("items", []):
                    if answer.get("score", 0) < MIN_SCORE_SE:
                        continue
                    body = answer.get("body", "")
                    if not body:
                        continue
                    code_blocks = self._extract_code_blocks(body)
                    q_title = q_map.get(str(answer.get("question_id", "")), "")

                    for code in code_blocks:
                        code_hash = hashlib.md5(code.encode()).hexdigest()
                        if code_hash in seen_hashes:
                            continue
                        seen_hashes.add(code_hash)
                        sub_chunks = _extract_bpy_functions(code)
                        if not sub_chunks:
                            sub_chunks = [code] if _has_bpy(code) else []
                        for chunk in sub_chunks:
                            if len(snippets) >= self.quota:
                                break
                            snippets.append(Snippet(
                                id=_make_id("se", chunk),
                                collection=_infer_collection(q_title + " " + chunk),
                                description=q_title or "Blender Python snippet from Stack Exchange",
                                code=chunk,
                                tags=_extract_tags(chunk, q_title),
                                source="stackexchange",
                                source_url=answer.get("link", ""),
                                score=answer.get("score", 0),
                            ))

                if not data.get("has_more", False):
                    break
                page += 1

        log.info(f"[SE] Collected {len(snippets)} snippets")
        return snippets


class GitHubCollector:
    """Collector for snippets from manually selected GitHub repos.

    Uses PyGitHub to access .py files from repos in GITHUB_REPOS
    and extract functions/methods with bpy calls.

    Requires a GitHub token for adequate rate limit (60 req/h anonymous
    barely suffices for a small repo).

    Args:
        token: GitHub personal access token (optional but recommended).
        quota: Maximum number of snippets to collect.
    """

    def __init__(self, token: str | None = None, quota: int = 200) -> None:
        if not _GITHUB_OK:
            raise ImportError("pip install PyGitHub")
        _token = token or os.environ.get("GITHUB_TOKEN")
        if _token:
            self.gh = Github(auth=Auth.Token(_token))
            log.info("[GH] Authenticated with token (5000 requests/hour)")
        else:
            self.gh = Github()
            log.warning("[GH] No token: anonymous access, 60 requests/hour.")
        self.quota = quota

    def _iter_py_files(self, repo) -> Iterator[tuple[str, str]]:
        """Iterates .py files in a repo, yielding (path, content).

        Skips test files, migrations, __pycache__, files > 100KB.
        Respects MAX_ITEMS_PER_REPO and MAX_SECONDS_PER_REPO.

        Args:
            repo: PyGitHub Repository object.

        Yields:
            Tuple (path, decoded_content) for each valid .py file.
        """
        repo_name = repo.full_name
        try:
            contents = _gh_call(self.gh, repo.get_contents, "")
        except GithubException as e:
            log.warning(f"[GH] {repo_name}: root not readable ({e})")
            return
        except RateLimitExceededException:
            log.error(f"[GH] {repo_name}: rate limit exhausted even after retries")
            return

        stack = list(contents)
        scanned = skipped = yielded = items_seen = 0
        repo_start = time.monotonic()

        while stack:
            if items_seen >= MAX_ITEMS_PER_REPO:
                log.warning(f"[GH] {repo_name}: limit of {MAX_ITEMS_PER_REPO} items reached")
                break
            if time.monotonic() - repo_start > MAX_SECONDS_PER_REPO:
                log.warning(f"[GH] {repo_name}: timeout {MAX_SECONDS_PER_REPO}s reached")
                break
            item = stack.pop()
            items_seen += 1
            try:
                if item.type == "dir":
                    stack.extend(_gh_call(self.gh, repo.get_contents, item.path))
                elif item.name.endswith(".py") and item.size < 100_000:
                    scanned += 1
                    if any(x in item.path for x in ["test", "migration", "__pycache__"]):
                        skipped += 1
                        continue
                    try:
                        content = item.decoded_content.decode("utf-8", errors="ignore")
                        yielded += 1
                        yield item.path, content
                    except Exception as e:
                        log.debug(f"[GH] {repo_name}/{item.path}: decode error ({e})")
            except RateLimitExceededException:
                log.error(f"[GH] {repo_name}: rate limit exhausted during scan")
                break
            except GithubException as e:
                log.debug(f"[GH] {repo_name}/{getattr(item, 'path', '?')}: {e}")

        log.info(f"[GH] {repo_name}: {scanned} .py files examined, {yielded} used, {skipped} skipped")

    def collect(self) -> list[Snippet]:
        """Performs collection from all repos in GITHUB_REPOS.

        Returns:
            List of collected snippets (deduplicated by hash).
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()

        for repo_name in GITHUB_REPOS:
            if len(snippets) >= self.quota:
                break

            log.info(f"[GH] Repo: {repo_name} ({len(snippets)}/{self.quota})")



            try:
                repo = _gh_call(self.gh, self.gh.get_repo, repo_name)
            except RateLimitExceededException:
                log.error("[GH] Rate limit exhausted, stopping")
                break
            except GithubException as e:
                status = getattr(e, "status", "?")
                log.warning(f"[GH] Repo {repo_name} not accessible (HTTP {status}): {e}")
                continue

            stars = getattr(repo, "stargazers_count", 0)

            try:
                for file_path, content in self._iter_py_files(repo):
                    if len(snippets) >= self.quota:
                        break
                    if not _has_bpy(content):
                        continue
                    for chunk in _extract_bpy_functions(content):
                        if len(snippets) >= self.quota:
                            break
                        code_hash = hashlib.md5(chunk.encode()).hexdigest()
                        if code_hash in seen_hashes:
                            continue
                        seen_hashes.add(code_hash)

                        first_line = chunk.strip().splitlines()[0]
                        func_match = re.search(r'def (\w+)', first_line)
                        func_label = func_match.group(1) if func_match else "snippet"
                        description = f"{func_label} from {repo_name}/{file_path}"

                        snippets.append(Snippet(
                            id=_make_id("gh", chunk),
                            collection=_infer_collection(description + " " + chunk),
                            description=description,
                            code=chunk,
                            tags=_extract_tags(chunk, description),
                            source="github",
                            source_url=f"https://github.com/{repo_name}/blob/HEAD/{file_path}",
                            score=stars,
                        ))
            except RateLimitExceededException:
                log.error("[GH] Rate limit exhausted during scan, stopping")
                break

        log.info(f"[GH] Collected {len(snippets)} snippets")
        return snippets


class GitHubSearchCollector:
    """Collector for snippets via GitHub Code Search API.

    Searches bpy patterns across ALL of GitHub (not just curated repos) using
    the Code Search API, which requires a token (does not work anonymously)
    and has a rate limit of 10 requests/minute.

    Args:
        token: GitHub personal access token (required).
        quota: Maximum number of snippets to collect.
    """

    DELAY = 7.0

    def __init__(self, token: str, quota: int = 200) -> None:
        if not _GITHUB_OK:
            raise ImportError("pip install PyGitHub")
        if not token:
            raise ValueError("GitHubSearchCollector requires a token: code search not available anonymously")
        self.gh = Github(auth=Auth.Token(token))
        self.quota = quota

    def collect(self) -> list[Snippet]:
        """Performs collection using all queries in GITHUB_SEARCH_QUERIES.

        Returns:
            List of collected snippets (deduplicated by hash and file).
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()
        seen_files: set[str] = set()

        for query in GITHUB_SEARCH_QUERIES:
            if len(snippets) >= self.quota:
                break
            log.info(f"[GH-SEARCH] Query: '{query}' ({len(snippets)}/{self.quota})")

            try:
                results = _gh_call(self.gh, self.gh.search_code, query)
            except Exception as e:
                log.warning(f"[GH-SEARCH] Search '{query}' failed: {e}")
                continue

            try:
                for item in results:
                    if len(snippets) >= self.quota:
                        break

                    file_key = f"{item.repository.full_name}/{item.path}"
                    if file_key in seen_files:
                        continue
                    seen_files.add(file_key)

                    if getattr(item, "size", 0) > 100_000:
                        continue

                    try:
                        content = _gh_call(self.gh, lambda: item.decoded_content.decode("utf-8", errors="ignore"))
                    except Exception as e:
                        log.debug(f"[GH-SEARCH] Skip {file_key}: {e}")
                        continue

                    if not _has_bpy(content):
                        continue

                    try:
                        stars = item.repository.stargazers_count
                    except Exception:
                        stars = 0

                    for chunk in _extract_bpy_functions(content):
                        if len(snippets) >= self.quota:
                            break
                        code_hash = hashlib.md5(chunk.encode()).hexdigest()
                        if code_hash in seen_hashes:
                            continue
                        seen_hashes.add(code_hash)

                        first_line = chunk.strip().splitlines()[0]
                        func_match = re.search(r'def (\w+)', first_line)
                        func_label = func_match.group(1) if func_match else "snippet"
                        description = f"{func_label} from {file_key}"

                        snippets.append(Snippet(
                            id=_make_id("ghs", chunk),
                            collection=_infer_collection(description + " " + chunk),
                            description=description,
                            code=chunk,
                            tags=_extract_tags(chunk, description),
                            source="github_search",
                            source_url=getattr(item, "html_url", ""),
                            score=stars,
                        ))

                    time.sleep(self.DELAY)
            except RateLimitExceededException:
                log.warning("[GH-SEARCH] Rate limit on search API, moving to next query")
                continue
            except GithubException as e:
                log.warning(f"[GH-SEARCH] Error during result iteration: {e}")
                continue

        log.info(f"[GH-SEARCH] Collected {len(snippets)} snippets")
        return snippets


class BlenderExamplesCollector:
    """Collector for official Python scripts from the blender/blender repo.

    Extracts from the scripts/templates_py folder (~80 official scripts)
    and from specific blender-addons subfolders (io_mesh_stl,
    object_print3d_toolbox, etc.).

    Unlike GitHubCollector, this accesses target directories directly
    without full recursive scanning.

    Args:
        token: Optional GitHub token.
        quota: Maximum number of snippets to collect.
    """

    def __init__(self, token: str | None = None, quota: int = 3000) -> None:
        if not _GITHUB_OK:
            raise ImportError("pip install PyGitHub")
        auth = Auth.Token(token) if token else None
        self.gh = Github(auth=auth) if auth else Github()
        self.quota = quota

    def _get_py_files_in_path(self, repo, path: str) -> list:
        """Returns all .py files in a folder and 1 level of subfolders.

        Args:
            repo: PyGitHub Repository object.
            path: Path in the repository.

        Returns:
            List of PyGitHub ContentFile objects (.py).
        """
        try:
            contents = _gh_call(self.gh, repo.get_contents, path)
        except Exception as e:
            log.warning(f"[EXAMPLES] Unable to access {repo.full_name}/{path}: {e}")
            return []
        files = []
        dirs = []
        if not isinstance(contents, list):
            contents = [contents]
        for item in contents:
            if item.type == "file" and item.name.endswith(".py"):
                files.append(item)
            elif item.type == "dir":
                dirs.append(item)
        for d in dirs[:20]:
            try:
                sub = _gh_call(self.gh, repo.get_contents, d.path)
                if not isinstance(sub, list):
                    sub = [sub]
                for item in sub:
                    if item.type == "file" and item.name.endswith(".py"):
                        files.append(item)
            except Exception as e:
                log.debug(f"[EXAMPLES] Skip subdir {d.path}: {e}")
        return files

    def collect(self) -> list[Snippet]:
        """Performs collection from all repos/paths in GITHUB_EXAMPLE_REPOS.

        Returns:
            List of snippets from official Blender templates.
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()

        for repo_name, paths in GITHUB_EXAMPLE_REPOS:
            if len(snippets) >= self.quota:
                break
            log.info(f"[EXAMPLES] Repo: {repo_name}")
            try:
                repo = _gh_call(self.gh, self.gh.get_repo, repo_name)
            except Exception as e:
                log.warning(f"[EXAMPLES] Unable to open repo {repo_name}: {e}")
                continue

            stars = repo.stargazers_count

            for path in paths:
                if len(snippets) >= self.quota:
                    break
                log.info(f"[EXAMPLES]   Path: {path}")
                py_files = self._get_py_files_in_path(repo, path)
                log.info(f"[EXAMPLES]   Found {len(py_files)} .py files")

                for file_item in py_files:
                    if len(snippets) >= self.quota:
                        break
                    try:
                        content = _gh_call(
                            self.gh,
                            lambda fi=file_item: fi.decoded_content.decode("utf-8", errors="ignore")
                        )
                    except Exception as e:
                        log.debug(f"[EXAMPLES] Skip {file_item.path}: {e}")
                        continue

                    if not _has_bpy(content):
                        continue

                    lines = content.splitlines()
                    if len(lines) <= MAX_CODE_LINES:
                        chunks = [content.strip()]
                    else:
                        chunks = _extract_bpy_functions(content)

                    for chunk in chunks:
                        if len(snippets) >= self.quota:
                            break
                        if not _has_bpy(chunk):
                            continue
                        code_hash = hashlib.md5(chunk.encode()).hexdigest()
                        if code_hash in seen_hashes:
                            continue
                        seen_hashes.add(code_hash)

                        description = f"Official Blender script: {repo_name}/{file_item.path}"
                        snippets.append(Snippet(
                            id=_make_id("examples", chunk),
                            collection=_infer_collection(description + " " + chunk),
                            description=description,
                            code=chunk,
                            tags=_extract_tags(chunk, description),
                            source="blender_examples",
                            source_url=file_item.html_url,
                            score=stars + 200,
                        ))

        log.info(f"[EXAMPLES] Collected {len(snippets)} snippets")
        return snippets


class BlenderArtistsCollector:
    """Collector for snippets from the Blender Artists forum (Discourse).

    Uses the public Discourse API to list topics in Python scripting
    categories and download posts with bpy code.

    No authentication required for public content.

    Args:
        quota: Maximum number of snippets to collect.
    """

    BASE_URL = "https://blenderartists.org"
    DELAY = 1.5

    CATEGORIES = [
        ("python-support", 5),
        ("released-scripts-and-themes", 10),
        ("add-on-releases", 79),
    ]

    def __init__(self, quota: int = 2000) -> None:
        self.quota = quota
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "BlenderCorpusBot/1.0 (corpus builder for bpy RAG)",
            "Accept": "application/json",
        })

    def _get_json(self, url: str) -> dict | None:
        """Executes a Discourse GET and returns parsed JSON.

        Args:
            url: Full URL of the Discourse endpoint.

        Returns:
            JSON dict or None if error/404.
        """
        time.sleep(self.DELAY)
        try:
            r = _request_with_retry(self.session, "GET", url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.debug(f"[BA] Fetch error {url}: {e}")
            return None

    def _extract_code_from_post(self, cooked_html: str) -> list[str]:
        """Extracts bpy code blocks from Discourse HTML (field 'cooked').

        Args:
            cooked_html: HTML of the Discourse post.

        Returns:
            List of cleaned code blocks.
        """
        soup = BeautifulSoup(cooked_html, "html.parser")
        blocks = []
        for tag in soup.find_all(["pre", "code"]):
            text = tag.get_text()
            if _has_bpy(text):
                cleaned = _clean_code(text)
                if cleaned:
                    blocks.append(cleaned)
        return blocks

    def _collect_from_topic(self, topic_id: int, topic_title: str,
                            seen_hashes: set[str]) -> list[Snippet]:
        """Collects snippets from a single Discourse thread.

        Args:
            topic_id: Numeric topic ID.
            topic_title: Topic title.
            seen_hashes: Set of hashes for global deduplication.

        Returns:
            List of snippets from the thread.
        """
        data = self._get_json(f"{self.BASE_URL}/t/{topic_id}.json")
        if not data:
            return []

        snippets = []
        posts = data.get("post_stream", {}).get("posts", [])

        for post in posts:
            cooked = post.get("cooked", "")
            if not cooked or "bpy" not in cooked:
                continue

            code_blocks = self._extract_code_from_post(cooked)
            post_url = f"{self.BASE_URL}/t/{topic_id}/{post.get('post_number', 1)}"
            post_score = post.get("score", 0) or 0

            for code in code_blocks:
                code_hash = hashlib.md5(code.encode()).hexdigest()
                if code_hash in seen_hashes:
                    continue
                seen_hashes.add(code_hash)
                sub_chunks = _extract_bpy_functions(code)
                if not sub_chunks:
                    sub_chunks = [code]
                for chunk in sub_chunks:
                    description = f"{topic_title} (Blender Artists)"


                    snippets.append(Snippet(
                        id=_make_id("ba", chunk),
                        collection=_infer_collection(description + " " + chunk),
                        description=description,
                        code=chunk,
                        tags=_extract_tags(chunk, description),
                        source="blender_artists",
                        source_url=post_url,
                        score=int(post_score),
                    ))

        return snippets

    def collect(self) -> list[Snippet]:
        """Performs collection from all configured Discourse categories.

        Returns:
            List of snippets from the Blender Artists forum.
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()
        seen_topic_ids: set[int] = set()

        for cat_slug, cat_id in self.CATEGORIES:
            if len(snippets) >= self.quota:
                break
            log.info(f"[BA] Category: {cat_slug} ({len(snippets)}/{self.quota})")

            page = 0
            consecutive_empty = 0

            while len(snippets) < self.quota and consecutive_empty < 3:
                url = f"{self.BASE_URL}/c/{cat_slug}/{cat_id}.json?page={page}"
                data = self._get_json(url)
                if not data:
                    break

                topic_list = data.get("topic_list", {})
                topics = topic_list.get("topics", [])
                if not topics:
                    break

                new_this_page = 0
                for topic in topics:
                    if len(snippets) >= self.quota:
                        break
                    tid = topic.get("id")
                    if tid in seen_topic_ids:
                        continue
                    seen_topic_ids.add(tid)

                    title = topic.get("title", "")
                    if not any(kw in title.lower() for kw in
                               ["python", "script", "addon", "bpy", "code", "blender api",
                                "mesh", "object", "modifier", "geometry"]):
                        if topic.get("posts_count", 0) < 5:
                            continue

                    new_snips = self._collect_from_topic(tid, title, seen_hashes)
                    if new_snips:
                        snippets.extend(new_snips)
                        new_this_page += len(new_snips)
                        log.info(f"[BA]   Topic '{title[:50]}': {len(new_snips)} snippets")

                if new_this_page == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                page += 1
                if page > 30:
                    break

        log.info(f"[BA] Collected {len(snippets)} snippets")
        return snippets


class BlenderDocsCollector:
    """Collector for snippets from the official Blender API documentation.

    Downloads HTML pages from docs.blender.org and extracts code examples
    from <div class="highlight"> blocks.

    In addition to the fixed BLENDER_DOCS_URLS list, automatically discovers
    bpy.types.* / bpy.ops.* sub-pages starting from the index pages.

    Args:
        quota: Maximum number of snippets to collect.
        discover: If True, automatically discovers pages from hubs.
        max_pages: Maximum number of docs pages to examine.
    """

    DELAY = 1.0

    def __init__(self, quota: int = 100, discover: bool = True, max_pages: int = 400) -> None:
        self.quota = quota
        self.discover = discover
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "BlenderCorpusBuilder/2.0"

    def _fetch_page(self, url: str) -> BeautifulSoup | None:
        """Downloads an HTML page from the documentation.

        Args:
            url: Full URL of the docs.blender.org page.

        Returns:
            Parsed BeautifulSoup, or None on error.
        """
        time.sleep(self.DELAY)
        try:
            r = _request_with_retry(self.session, "GET", url)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log.warning(f"[DOCS] Fetch error {url}: {e}")
            return None

    def _discover_urls(self) -> list[str]:
        """Explores index pages and collects links to reference sub-pages.

        Returns:
            List of docs page URLs to examine.
        """
        discovered: set[str] = set(BLENDER_DOCS_URLS)
        base = "https://docs.blender.org/api/current/"

        for hub_url in BLENDER_DOCS_HUB_URLS:
            if len(discovered) >= self.max_pages:
                break
            soup = self._fetch_page(hub_url)
            if not soup:
                continue
            for a in soup.find_all("a", href=True):
                href = a["href"].split("#")[0]
                if not href.endswith(".html"):
                    continue
                full_url = href if href.startswith("http") else base + href
                if full_url.startswith(base) and (
                    "bpy.types." in full_url
                    or "bpy.ops." in full_url
                    or "bmesh" in full_url
                    or "mathutils" in full_url
                    or "gpu." in full_url
                ):
                    discovered.add(full_url)
                if len(discovered) >= self.max_pages:
                    break

        log.info(f"[DOCS] {len(discovered)} pages to examine")
        return list(discovered)

    def _extract_examples(self, soup: BeautifulSoup, page_url: str) -> list[tuple[str, str]]:
        """Extracts (description, code) pairs from the parsed HTML page.

        Looks for <pre> blocks inside <div class="highlight"> or with
        class="literal-block", characteristic of Sphinx documentation.

        Args:
            soup: BeautifulSoup of the page.
            page_url: Page URL (for fallback description).

        Returns:
            List of (description, code) tuples.
        """
        results = []
        seen_in_page: set[str] = set()

        candidates: list = []
        for div in soup.find_all("div", class_=True):
            cls_str = " ".join(div.get("class", []))
            if "highlight" in cls_str or "literal-block" in cls_str:
                pre = div.find("pre")
                if pre and pre not in candidates:
                    candidates.append(pre)
        for pre in soup.find_all("pre", class_=lambda c: c and "literal-block" in " ".join(c)):
            if pre not in candidates:
                candidates.append(pre)

        for pre_tag in candidates:
            code = _clean_code(pre_tag.get_text())
            if len(code.splitlines()) < 2:
                continue
            if not _has_bpy(code) and "import bpy" not in code:
                continue

            code_key = code[:200]
            if code_key in seen_in_page:
                continue
            seen_in_page.add(code_key)

            description = ""
            for sibling in pre_tag.find_all_previous(["h1", "h2", "h3", "h4", "p"], limit=3):
                text = sibling.get_text().strip()
                if text and len(text) > 10:
                    description = text[:200]
                    break
            if not description:
                description = f"Example from Blender API documentation: {page_url}"

            results.append((description, code))
        return results

    def collect(self) -> list[Snippet]:
        """Performs collection from all discovered or fixed docs pages.

        Returns:
            List of snippets from the official documentation.
        """
        snippets: list[Snippet] = []
        seen_hashes: set[str] = set()

        urls = self._discover_urls() if self.discover else list(BLENDER_DOCS_URLS)

        for url in urls:
            if len(snippets) >= self.quota:
                break

            log.info(f"[DOCS] {url} ({len(snippets)}/{self.quota})")


            soup = self._fetch_page(url)
            if not soup:
                continue

            for description, code in self._extract_examples(soup, url):
                if len(snippets) >= self.quota:
                    break

                code_hash = hashlib.md5(code.encode()).hexdigest()
                if code_hash in seen_hashes:
                    continue
                seen_hashes.add(code_hash)

                snippets.append(Snippet(
                    id=_make_id("docs", code),
                    collection=_infer_collection(description + " " + code),
                    description=description,
                    code=code,
                    tags=_extract_tags(code, description),
                    source="blender_docs",
                    source_url=url,
                    score=100,
                ))

        log.info(f"[DOCS] Collected {len(snippets)} snippets")
        return snippets


def quality_filter(snippets: list[Snippet]) -> list[Snippet]:
    """Filters and deduplicates the final corpus for quality.

    Rejection criteria:
      - Code with Python SyntaxError
      - Fewer than MIN_BPY_CALLS bpy.* calls
      - Duplicates (normalized hash)
      - Placeholders (TODO, FIXME, NotImplementedError, pass#)
      - Too short (fewer than MIN_CODE_LINES)

    Args:
        snippets: List of snippets to filter.

    Returns:
        Filtered and deduplicated list.
    """
    seen: set[str] = set()
    accepted: list[Snippet] = []
    rejected_reasons: dict[str, int] = {}

    def _reject(reason: str) -> None:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    for s in snippets:
        normalized = re.sub(r'\s+', ' ', s.code).strip()
        h = hashlib.md5(normalized.encode()).hexdigest()
        if h in seen:
            _reject("duplicate")
            continue
        seen.add(h)

        try:
            ast.parse(s.code)
        except SyntaxError:
            _reject("syntax_error")
            continue

        if s.code.count("bpy.") < MIN_BPY_CALLS:
            _reject("no_bpy")
            continue

        placeholder_patterns = [
            r'\.\.\.\s*$',
            r'#\s*TODO',
            r'#\s*FIXME',
            r'pass\s*#',
            r'raise NotImplementedError',
        ]
        if any(re.search(p, s.code, re.MULTILINE) for p in placeholder_patterns):
            _reject("placeholder")
            continue

        if len(s.code.splitlines()) < MIN_CODE_LINES:
            _reject("too_short")
            continue

        accepted.append(s)

    log.info(f"[FILTER] Accepted: {len(accepted)} | Rejected due to:")
    for reason, count in sorted(rejected_reasons.items(), key=lambda x: -x[1]):
        log.info(f"  - {reason}: {count}")

    return accepted


def save_corpus(snippets: list[Snippet], path: Path = CORPUS_PATH) -> None:
    """Saves the filtered corpus to disk in JSONL format.

    Args:
        snippets: List of snippets to save.
        path: Path of the JSONL output file.
    """
    with open(path, "w", encoding="utf-8") as f:
        for s in snippets:
            f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    log.info(f"[CORPUS] Saved {len(snippets)} snippets to {path}")


def load_corpus(path: Path = CORPUS_PATH) -> list[Snippet]:
    """Loads the corpus from a JSONL file.

    Args:
        path: Path of the JSONL file to read.

    Returns:
        List of deserialized Snippets.
    """
    snippets = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                snippets.append(Snippet.from_dict(json.loads(line)))
    log.info(f"[CORPUS] Loaded {len(snippets)} snippets from {path}")
    return snippets


async def load_corpus_into_vectordb(db, path: Path = CORPUS_PATH) -> None:
    """Loads corpus.jsonl into the Chroma VectorDB.

    Adds external corpus snippets (collected by corpus_builder)
    to the existing VectorDB, alongside the internal CORPUS in vectordb.py.

    Args:
        db: VectorDB instance.
        path: Path to the corpus.jsonl file.
    """
    snippets = load_corpus(path)

    corpus_dicts = [
        {
            "id":          s.id,
            "collection":  s.collection,
            "description": s.description,
            "code":        s.code,
            "tags":        s.tags,
        }
        for s in snippets
    ]

    if hasattr(db, "_index_snippets"):
        await db._index_snippets(corpus_dicts)
    else:
        import vectordb
        original_len = len(vectordb.CORPUS)
        vectordb.CORPUS.extend(corpus_dicts)
        log.info(f"[CORPUS] Added {len(corpus_dicts)} snippets to CORPUS ({original_len} -> {len(vectordb.CORPUS)})")
        await db.build()


def build_corpus(
    github_token: str | None = None,
    se_api_key: str | None = None,
    skip_github: bool = False,
    skip_github_search: bool = False,
    skip_blender_artists: bool = False,
    discover_docs: bool = True,
    max_doc_pages: int = 400,
) -> list[Snippet]:
    """Runs all collectors and returns the filtered corpus.

    Coordinates the execution of each collector, applies the quality filter,
    and produces the final list sorted by score.

    Args:
        github_token: Optional GitHub token (without: no code search,
            curated repos very limited).
        se_api_key: Optional Stack Exchange API key.
        skip_github: True to skip GitHub (curated + search).
        skip_github_search: True to skip only code search.
        skip_blender_artists: True to skip the forum.
        discover_docs: True to auto-discover docs pages.
        max_doc_pages: Maximum docs pages to examine.

    Returns:
        Final list of filtered snippets sorted by score.
    """
    all_snippets: list[Snippet] = []
    token = github_token or os.environ.get("GITHUB_TOKEN")
    se_key = se_api_key or os.environ.get("STACKEXCHANGE_KEY")
    raw_by_source: Counter = Counter()

    log.info("=== CREDENTIALS ===")
    log.info(f"  GITHUB_TOKEN:       {'present' if token else 'MISSING (60 req/h anonymous, no code search)'}")
    log.info(f"  STACKEXCHANGE_KEY:  {'present' if se_key else 'MISSING (300 req/day per IP, shared)'}")

    def _collect_source(name: str, label: str, fn) -> None:
        try:
            result = fn()
        except ImportError as e:
            log.error(f"[{label}] {e} -- skipping")
            return
        except Exception:
            log.exception(f"[{label}] Collector failed")
            return
        raw_by_source[name] += len(result)
        log.info(f"[{label}] Collected (raw): {len(result)}")
        all_snippets.extend(result)

    log.info("=== COLLECTOR: Blender API Docs ===")
    _collect_source(
        "blender_docs", "DOCS",
        lambda: BlenderDocsCollector(
            quota=QUOTA["blender_docs"], discover=discover_docs, max_pages=max_doc_pages
        ).collect(),
    )

    log.info("=== COLLECTOR: Blender Official Examples (GitHub) ===")
    _collect_source(
        "blender_examples", "EXAMPLES",
        lambda: BlenderExamplesCollector(token=token, quota=QUOTA["blender_examples"]).collect(),
    )

    log.info("=== COLLECTOR: Blender Artists Forum ===")
    if not skip_blender_artists:
        _collect_source(
            "blender_artists", "BA",
            lambda: BlenderArtistsCollector(quota=QUOTA["blender_artists"]).collect(),
        )
    else:
        log.info("=== COLLECTOR: Blender Artists (skipped) ===")

    log.info("=== COLLECTOR: Stack Exchange ===")
    _collect_source(
        "stackexchange", "SE",
        lambda: StackExchangeCollector(quota=QUOTA["stackexchange"], api_key=se_key).collect(),
    )

    if not skip_github:
        log.info("=== COLLECTOR: GitHub (curated repos) ===")
        _collect_source(
            "github", "GH",
            lambda: GitHubCollector(token=token, quota=QUOTA["github_repos"]).collect(),
        )

        if not skip_github_search:
            log.info("=== COLLECTOR: GitHub Code Search ===")
            if not token:
                log.warning("[GH-SEARCH] No token available: code search requires authentication, skipping")
            else:
                _collect_source(
                    "github_search", "GH-SEARCH",
                    lambda: GitHubSearchCollector(token=token, quota=QUOTA["github_search"]).collect(),
                )
    else:
        log.info("=== COLLECTOR: GitHub (skipped) ===")

    log.info(f"[RAW] Total snippets collected: {len(all_snippets)}")

    log.info("=== QUALITY FILTER ===")
    filtered = quality_filter(all_snippets)

    filtered.sort(key=lambda s: s.score, reverse=True)
    final = filtered[:TARGET_TOTAL]

    accepted_by_source = Counter(s.source for s in filtered)
    log.info("=== COLLECTED vs ACCEPTED BY SOURCE (diagnostics) ===")
    for name in raw_by_source:
        raw_n = raw_by_source[name]
        kept_n = accepted_by_source.get(name, 0)
        pct = (kept_n / raw_n * 100) if raw_n else 0.0
        log.info(f"  {name}: {raw_n} collected -> {kept_n} accepted ({pct:.0f}%)")

    dist = Counter(s.collection for s in final)
    src_dist = Counter(s.source for s in final)
    log.info(f"[FINAL CORPUS] {len(final)} snippets")
    for coll, count in dist.most_common():
        log.info(f"  collection={coll}: {count}")
    for src, count in src_dist.most_common():
        log.info(f"  source={src}: {count}")

    return final


async def _main_async(args: argparse.Namespace) -> None:
    """Runs the complete pipeline based on CLI flags.

    Args:
        args: Argparse namespace with configuration flags.
    """
    if args.index_only:
        from vectordb import VectorDB
        db = VectorDB()
        await db.build()
        await load_corpus_into_vectordb(db)
        return

    if args.load_only and CORPUS_PATH.exists():
        snippets = load_corpus()
    else:
        snippets = build_corpus(
            github_token=args.github_token,
            se_api_key=args.se_key,
            skip_github=args.no_github,
            skip_github_search=args.no_github_search,
            skip_blender_artists=args.no_blender_artists,
            discover_docs=not args.no_docs_discover,
            max_doc_pages=args.max_doc_pages,
        )
        save_corpus(snippets)

    if args.index:
        from vectordb import VectorDB
        db = VectorDB()
        await db.build()
        await load_corpus_into_vectordb(db)


def main() -> None:
    """CLI entry point for corpus_builder.

    Usage:
        python corpus_builder.py                          # full collection
        python corpus_builder.py --load-only               # reuse corpus.jsonl
        python corpus_builder.py --index-only              # indexing only
        python corpus_builder.py -v                        # verbose logging
    """
    parser = argparse.ArgumentParser(description="Corpus builder for bpy snippets")
    parser.add_argument("--load-only", action="store_true", help="Reuse existing corpus.jsonl")
    parser.add_argument("--index-only", action="store_true", help="Indexing only (corpus.jsonl already present)")
    parser.add_argument("--index", action="store_true", help="Index after collection")
    parser.add_argument("--no-blender-artists", action="store_true", help="Skip Blender Artists forum")
    parser.add_argument("--no-github", action="store_true", help="Skip GitHub (curated repos + code search)")
    parser.add_argument("--no-github-search", action="store_true", help="Skip only dynamic code search")
    parser.add_argument("--no-docs-discover", action="store_true", help="Don't auto-discover docs pages")
    parser.add_argument("--max-doc-pages", type=int, default=400, help="Max docs pages")
    parser.add_argument("--github-token", type=str, default=None, help="GitHub token (or env GITHUB_TOKEN)")
    parser.add_argument("--se-key", type=str, default=None, help="Stack Exchange key (or env STACKEXCHANGE_KEY)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    _load_dotenv()

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
