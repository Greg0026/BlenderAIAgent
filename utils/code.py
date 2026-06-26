"""Utilities for extracting and manipulating Python code from LLM responses.

Provides functions for:
  - Extracting Python code from LLM responses (containing markdown, explanations)
  - Extracting traceback/error sections from Blender output
  - Formatting errors for semantic VectorDB queries
  - Truncating long errors to a manageable length for LLM context

These functions are used by core/llm.py, core/orchestrator.py and
pipeline phases to process model responses.
"""

import re
from typing import Optional


def extract_code(text: str) -> Optional[str]:
    """Extracts Python code from an LLM response that may contain markdown.

    Extraction strategy (in order):
    1. Look for ```python ... ``` or ``` ... ``` blocks (fence)
    2. Look for inline `...` code with import bpy
    3. Look for lines starting with import bpy/bmesh or containing bpy.
       WARNING: avoids false positives on comments (# bpy...) thanks
       to the \\bbpy\\. regex pattern (word boundary).

    Args:
        text: LLM text response (may contain explanations).

    Returns:
        Extracted Python code, or None if no valid code detected.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    code_fence = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if code_fence:
        candidate = code_fence.group(1).strip()
        if candidate:
            return candidate

    inline = re.search(r"`([^`]+)`", text)
    if inline:
        candidate = inline.group(1).strip()
        if candidate and ("import bpy" in candidate or "bpy." in candidate):
            return candidate

    lines = text.split("\n")
    code_lines = []
    started = False
    bpy_ref = re.compile(r"\b(import bpy\b|bpy\.|bmesh\.)")
    for line in lines:
        stripped = line.strip()
        if not started:
            if stripped.startswith("import bpy") or stripped.startswith("import bmesh") or stripped.startswith("from bpy"):
                started = True
                code_lines.append(line)
            elif bpy_ref.search(stripped) and not stripped.startswith("#") and not stripped.startswith("//"):
                started = True
                code_lines.append(line)
        elif stripped.startswith("#") or stripped.startswith("//"):
            code_lines.append(line)
        elif stripped:
            code_lines.append(line)
        else:
            code_lines.append(line)

    code = "\n".join(code_lines).strip()
    if code and ("import bpy" in code or "bpy." in code):
        return code

    non_comment_lines = [l for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("//")]
    non_comment_text = "\n".join(non_comment_lines)
    if "import bpy" in non_comment_text or "bpy." in non_comment_text:
        return non_comment_text

    return None


def extract_error_section(output: str, max_lines: int = 60) -> str:
    """Extracts the traceback section from Blender output.

    Searches for the "Traceback (most recent call last):" marker and returns
    the following lines up to max_lines. If no traceback is found,
    returns the last max_lines lines of the output.

    Args:
        output: Full Blender output (stdout + stderr).
        max_lines: Maximum number of lines to include.

    Returns:
        Traceback section or last lines of the output.
    """
    lines = output.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines)
            if "Traceback (most recent call last):" in line
            or "Python: Traceback" in line
        )
        return "\n".join(lines[start:start + max_lines])
    except StopIteration:
        return "\n".join(lines[-max_lines:])


def format_error_for_query(error_text: str) -> str:
    """Formats an error for use as a VectorDB query.

    Extracts the most significant lines from the error (Error, Warning, Exception)
    to create a compact semantic query. Avoids using the entire stack
    trace (dozens of lines) as a query, which would produce irrelevant results
    from the vector database.

    Args:
        error_text: Full error text (stack trace).

    Returns:
        Compact query (max 5 key lines) for vector search.
    """
    lines = error_text.splitlines()
    key_lines = [l for l in lines if "Error:" in l or "Warning:" in l or "Exception:" in l or l.strip().startswith("File")]
    if key_lines:
        return " ".join(key_lines[:5])
    return " ".join(lines[-3:])


def summarize_error(error_text: str, max_len: int = 300) -> str:
    """Truncates a long error to a maximum length for LLM context.

    Takes the last max_len characters (where the most useful part
    of the stack trace is: the error message and the last calls).

    Args:
        error_text: Full error text.
        max_len: Maximum character length of the result.

    Returns:
        Truncated error (from the end).
    """
    if len(error_text) <= max_len:
        return error_text
    return error_text[-max_len:]
