import asyncio
from typing import Dict, Optional

from cfg import CFG
from core.llm import LLMClient, get_prompt
from log import logger

_TEMP_LOW = 0.05
_TEMP_MED = 0.2
_TEMP_HIGH = 0.4


_FIX_CACHE: Dict[str, str] = {}
_FIX_CACHE_MAX = 64


def _cache_key(script: str, error_text: str, run_id: str = "") -> str:
    import hashlib
    return hashlib.md5((run_id + script + error_text).encode()).hexdigest()


def _safe_format(template: str, **kwargs: str) -> str:
    escaped = {}
    for key, val in kwargs.items():
        escaped[key] = val.replace("{", "{{").replace("}", "}}")
    return template.format(**escaped)


async def f1_enhance(llm: LLMClient, original_prompt: str) -> str:
    sys_prompt = get_prompt("f1_enhance.txt")
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": original_prompt}],
        label="F1_ENHANCE",
        do_extract_code=False,
        temperature=_TEMP_MED,
    )


async def f15_math_planner(llm: LLMClient, enhanced_prompt: str, original_prompt: str) -> str:
    sys_prompt = get_prompt("f15_math_planner.txt")
    user_msg = f"SPECIFICA TECNICA:\n{enhanced_prompt}\n\nPROMPT ORIGINALE:\n{original_prompt}"
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F1.5_MATH",
        do_extract_code=False,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("max_tokens", 16384),
    )


async def f2_codegen(
    llm: LLMClient,
    enhanced_prompt: str,
    math_plan: str,
    doc_ctx: str,
) -> str:
    sys_template = get_prompt("f2_codegen.txt")
    pitfalls = get_prompt("common_pitfalls.txt")
    sys_prompt = _safe_format(
        sys_template,
        doc_ctx=doc_ctx or "(nessuna documentazione rilevante trovata)",
        pitfalls=pitfalls,
    )
    user_msg = f"SPECIFICA TECNICA:\n{enhanced_prompt}\n\nPIANO ALGORITMICO:\n{math_plan}"
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F2_CODEGEN",
        do_extract_code=True,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("f2_max_tokens", 40000),
    )


async def f3a_morph_review(
    llm: LLMClient,
    script: str,
    enhanced_prompt: str,
    math_plan: str,
    original_prompt: str,
    prior_vision_feedback: str = "",
) -> str:
    sys_prompt = get_prompt("f3a_morph.txt")
    parts = [
        f"PROMPT ORIGINALE: {original_prompt}",
        f"SPECIFICA TECNICA: {enhanced_prompt}",
        f"PIANO ALGORITMICO: {math_plan}",
    ]
    if prior_vision_feedback:
        parts.append(f"FIX VISIVI GIÀ APPLICATI (NON DEFARE):\n{prior_vision_feedback}")
    parts.append(f"SCRIPT DA REVISIONARE:\n```python\n{script}\n```")
    user_msg = "\n\n".join(parts)
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F3A_MORPH",
        do_extract_code=True,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("f2_max_tokens", 40000),
    )


async def f3b_printability_review(
    llm: LLMClient,
    script: str,
    enhanced_prompt: str,
    math_plan: str,
    prior_vision_feedback: str = "",
) -> str:
    sys_prompt = get_prompt("f3b_printability.txt")
    parts = [
        f"SPECIFICA TECNICA:\n{enhanced_prompt}",
        f"PIANO ALGORITMICO:\n{math_plan}",
    ]
    if prior_vision_feedback:
        parts.append(f"FIX VISIVI GIÀ APPLICATI (NON DEFARE):\n{prior_vision_feedback}")
    parts.append(f"SCRIPT DA REVISIONARE:\n```python\n{script}\n```")
    user_msg = "\n\n".join(parts)
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F3B_PRINT",
        do_extract_code=True,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("f2_max_tokens", 40000),
    )


_run_id: str = ""


def _set_run_id(run_id: str) -> None:
    global _run_id
    _run_id = run_id


async def f6_targeted_fix(
    llm: LLMClient,
    script: str,
    error: str,
    doc_ctx: str,
    error_history: str,
) -> str:
    ck = _cache_key(script, error, _run_id)
    if ck in _FIX_CACHE:
        logger.info("F6 fix cache HIT per errore: %s...", error[:60])
        return _FIX_CACHE[ck]

    if len(_FIX_CACHE) >= _FIX_CACHE_MAX:
        _FIX_CACHE.clear()

    sys_template = get_prompt("f6_fix.txt")
    pitfalls = get_prompt("common_pitfalls.txt")
    sys_prompt = _safe_format(
        sys_template,
        error=error,
        doc_ctx=doc_ctx or "(nessuna documentazione rilevante)",
        pitfalls=pitfalls,
        error_history=error_history,
    )
    user_msg = f"SCRIPT DA CORREGGERE:\n```python\n{script}\n```"
    result = await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F6_FIX",
        do_extract_code=True,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("f2_max_tokens", 40000),
    )

    _FIX_CACHE[ck] = result
    return result


async def f6_vision_fix(
    llm: LLMClient,
    script: str,
    vision_report: str,
    error_history: str,
) -> str:
    sys_template = get_prompt("f6_vis_fix.txt")
    sys_prompt = _safe_format(sys_template, vision_report=vision_report, error_history=error_history)
    user_msg = f"SCRIPT DA CORREGGERE:\n```python\n{script}\n```"
    return await llm.call(
        system=sys_prompt,
        messages=[{"role": "user", "content": user_msg}],
        label="F6_VIS_FIX",
        do_extract_code=True,
        temperature=_TEMP_LOW,
        max_tokens=CFG.get("f2_max_tokens", 40000),
    )
