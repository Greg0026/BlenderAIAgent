import asyncio
import json
import os
import re
import sys
import time
import random
import traceback
from datetime import datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfg import CFG
from log import logger
from core.orchestrator import Orchestrator
from utils.runner import BlenderRunner
from vectordb.vectordb import VectorDB

BATCH_CFG = {
    "output_dir":         os.environ.get("BATCH_OUTPUT_DIR", "output_scripts"),
    "pause_between_jobs": int(os.environ.get("BATCH_PAUSE", "10")),
    "skip_failed":        True,
    "max_jobs":           CFG.get("batch_max_jobs"),
        "llm_model":          CFG.get("batch_llm_model", "openai/gpt-oss-120b:free"),
        "prompt_categories": [
        "low poly furniture",
        "smartphone and tablet accessories",
        "protective cases for electronic boards (Raspberry/Arduino)",
        "modular desk organizers",
        "desktop SD card and USB drive holders",
        "headphone or controller stands",
        "ergonomic laptop stands",
        "plant pots with voronoi or fractal design",
        "lampshades with parametric and perforated design",
        "abstract sculptures based on trigonometric functions",
        "platonic solids and complex geometries",
        "hook and grip accessories for gym equipment",
        "cylindrical sports containers with screw cap",
        "action cam mounts",
        "interlocking geometric 3D puzzles",
        "polyhedral game dice",
        "sci-fi and cyberpunk scenery elements",
        "replicas of mechanical artifacts and props",
        "lamp holders and bases for desk lamps",
        "bathroom electric toothbrush holders",
        "kitchen magnetic knife holders",
        "modular stackable storage boxes",
        "photo frames with geometric design",
        "desk ashtrays with engravings",
        "cable organizers and cable clips for desk",
        "bookends with abstract or animal shapes",
        "rotating desk pencil holders",
        "straps for electrical cable organization",
        "wall tablet mounts",
        "customizable wall key holders",
        "automatic medicine dispensers",
        "bedside eyeglass holders",
        "jewelry boxes with compartments",
        "car smartphone mounts with suction cup",
        "bedside watch holder",
        "mini pots for succulents with drainage",
        "pen holder with rotating base",
        "wall hooks with dual attachment",
        "small-scale architectural models",
        "parametric 3D dreamcatchers",
        "functional decorative gears",
        "3D printed LED desk lamps",
        "3D relief maps",
    ]
}


_HTTP_SESSION: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        _HTTP_SESSION = aiohttp.ClientSession()
    return _HTTP_SESSION


async def generate_dynamic_prompt(api_key: str, category: str) -> str:
    session = await _get_session()
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    sys_prompt = (
        "You are a technical assistant expert in procedural 3D modeling. "
        "Generate short and direct descriptions for Blender 3.0 Python scripts."
    )
    user_prompt = (
        f"Generate a single instruction (maximum 2 sentences) to model a 3D object in the category '{category}'. "
        "Include specific dimensions (e.g. cm or mm) and physical details (e.g. thickness, cavity, radius). "
        "Respond ONLY with the instruction, without quotes, without pleasantries and without code. "
        "Always start with 'Create a...' or 'Generate a...' and end the sentence with 'Blender 3.0.'."
    )
    data = {
        "model": BATCH_CFG["llm_model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.8,
        "max_tokens": 100
    }
    try:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                text = await response.text()
                logger.warning("LLM error (%s): %s", response.status, text)
    except Exception as e:
        logger.warning("LLM call exception: %s", e)
    return f"Create an object of type {category} with standard dimensions, 2mm walls. Blender 3.0."


def _slug(text: str) -> str:
    words = re.sub(r"[^\w\s]", "", text.lower()).split()
    stop = {"create", "a", "with", "from", "for", "of", "in", "blender", "30",
            "printable", "3d", "walls", "height", "about", "mm", "cm", "generate", "the"}
    meaningful = [w for w in words if w not in stop and not w.isdigit()][:6]
    return "_".join(meaningful) if meaningful else "generic_object"


def _elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def save_result(session_dir: Path, job_idx: int, prompt: str, script: str, success: bool, phase_reached: str, elapsed: float, error: str = "") -> Path:
    slug = _slug(prompt)
    folder_name = f"{job_idx:04d}_{slug}"
    job_dir = session_dir / folder_name
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "script.py").write_text(script, encoding="utf-8")
    (job_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    meta = {
        "job_index": job_idx, "prompt": prompt, "success": success,
        "phase_reached": phase_reached, "elapsed_sec": round(elapsed, 2),
        "elapsed_human": _elapsed(elapsed), "timestamp": datetime.now().isoformat(), "error": error,
    }
    (job_dir / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return job_dir


def write_session_report(session_dir: Path, results: list, total_elapsed: float):
    n_total = len(results)
    n_ok = sum(1 for r in results if r["success"])
    n_fail = n_total - n_ok
    rate = f"{n_ok/n_total*100:.1f}%" if n_total else "N/A"
    report = {
        "session_dir": str(session_dir), "timestamp": datetime.now().isoformat(),
        "total_jobs": n_total, "succeeded": n_ok, "failed": n_fail,
        "success_rate": rate, "total_elapsed": _elapsed(total_elapsed),
        "avg_per_job": _elapsed(total_elapsed / n_total) if n_total else "N/A",
        "jobs": results,
    }
    (session_dir / "session_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "BlenderAIAgent -- Batch Session Report",
        "=" * 60,
        f"Date/Time:        {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Folder:        {session_dir}",
        f"Jobs run:    {n_total}",
        f"Succeeded:        {n_ok}",
        f"Failed:         {n_fail}",
        f"Success rate:  {rate}",
        f"Total time:    {_elapsed(total_elapsed)}",
        f"Average per job:   {_elapsed(total_elapsed / n_total) if n_total else 'N/A'}",
        "", "JOB DETAILS", "-" * 60,
    ]
    for r in results:
        icon = "OK" if r["success"] else "FAIL"
        phase = r.get("phase_reached", "?")
        t = r.get("elapsed_human", "?")
        p = r["prompt"][:70] + ("..." if len(r["prompt"]) > 70 else "")
        lines.append(f"  {icon}  [{r['job_index']:04d}]  {t:>6}  [{phase}]")
        lines.append(f"       {p}")
        if r.get("error"):
            lines.append(f"       ERR: {r['error'][:100]}")
        lines.append("")
    (session_dir / "session_report.txt").write_text("\n".join(lines), encoding="utf-8")


async def run_single_job(orch: Orchestrator, prompt: str, job_idx: int) -> dict:
    logger.info("JOB %d -- %s", job_idx, prompt[:70])
    t0 = time.time()
    script, error, success, phase = "", "", False, "UNKNOWN"
    try:
        script = await orch.run(prompt, bver="3.0")
        if script and "import bpy" in script:
            success = True
            phase = "PIPELINE_OK"
        else:
            error = "Empty or invalid script returned by the agent."
    except Exception as e:
        error = str(e)
        traceback.print_exc()
    elapsed = time.time() - t0
    return {
        "job_index": job_idx, "prompt": prompt, "success": success,
        "phase_reached": phase, "elapsed_sec": round(elapsed, 2),
        "elapsed_human": _elapsed(elapsed), "script": script, "error": error,
    }


async def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        logger.critical("OPENROUTER_API_KEY not found (for batch prompt generation).")
        return
    if not nvidia_key:
        logger.warning("NVIDIA_API_KEY not found. The pipeline will use only OPENROUTER_API_KEY via LLM_BASE_URL.")

    session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = Path(BATCH_CFG["output_dir"]) / f"session_{session_ts}"
    session_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Session folder: %s", session_dir.resolve())

    blender_path = os.environ.get("BLENDER_PATH", "blender")
    runner = BlenderRunner(blender_executable=blender_path)
    db = VectorDB()
    try:
        await db.build()
    except Exception as e:
        logger.warning("VectorDB build failed (%s). Continuing without.", e)

    try:
        orch = Orchestrator(db, runner)
    except EnvironmentError as e:
        logger.critical("Config error: %s", e)
        return

    results = []
    session_start = time.time()
    n_ok = 0
    idx = 1
    max_jobs = BATCH_CFG.get("max_jobs")
    logger.info("Starting loop. Press CTRL+C to stop.")

    try:
        while True:
            if max_jobs and idx > max_jobs:
                logger.info("Maximum job limit (%s) reached.", max_jobs)
                break

            category = random.choice(BATCH_CFG["prompt_categories"])
            logger.info("Generating prompt (Category: %s)...", category)
            prompt = await generate_dynamic_prompt(api_key, category)

            result = await run_single_job(orch, prompt, idx)
            results.append({k: v for k, v in result.items() if k != "script"})

            if result["success"] and result["script"]:
                job_dir = save_result(session_dir, idx, prompt, result["script"], True, result["phase_reached"], result["elapsed_sec"])
                n_ok += 1
                logger.info("Script saved in: %s", job_dir)
            else:
                save_result(session_dir, idx, prompt, result["script"] or "# No script", False, result["phase_reached"], result["elapsed_sec"], result["error"])
                logger.warning("Job %d failed: %s", idx, result['error'][:80] if result['error'] else 'Unknown error')
                if not BATCH_CFG.get("skip_failed", True):
                    logger.warning("skip_failed=False -- aborting batch.")
                    break

            total_elapsed = time.time() - session_start
            logger.info("Progress: Job %d | OK %d | FAIL %d | Elapsed: %s", idx, n_ok, idx - n_ok, _elapsed(total_elapsed))
            write_session_report(session_dir, results, total_elapsed)

            pause = BATCH_CFG.get("pause_between_jobs", 10)
            logger.info("Pausing %ds...", pause)
            await asyncio.sleep(pause)
            idx += 1

    except KeyboardInterrupt:
        logger.info("User interruption. Saving report...")

    global _HTTP_SESSION
    if _HTTP_SESSION is not None:
        await _HTTP_SESSION.close()
        _HTTP_SESSION = None

    total_elapsed = time.time() - session_start
    write_session_report(session_dir, results, total_elapsed)

    n_total = len(results)
    logger.info("=" * 50)
    logger.info("SESSION COMPLETED")
    logger.info("=" * 50)
    logger.info("Job: %d | OK: %d | FAIL: %d | Rate: %s | Time: %s",
                n_total, n_ok, n_total - n_ok,
                f"{n_ok/n_total*100:.1f}%" if n_total else "N/A",
                _elapsed(total_elapsed))
    logger.info("Output: %s", session_dir.resolve())


if __name__ == "__main__":
    asyncio.run(main())
