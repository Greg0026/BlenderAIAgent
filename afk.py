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
        "arredamento low poly",
        "accessori per smartphone e tablet",
        "case protettive per schede elettroniche (Raspberry/Arduino)",
        "organizer da scrivania modulari",
        "porta-schede SD e pendrive da tavolo",
        "supporti per cuffie o controller",
        "stand ergonomici per laptop",
        "vasi per piante con design voronoi o frattale",
        "paralumi dal design parametrico e traforato",
        "sculture astratte basate su funzioni trigonometriche",
        "solidi platonici e geometrie complesse",
        "accessori ganci e grip per attrezzatura da palestra",
        "contenitori cilindrici sportivi con tappo a vite",
        "supporti per action cam",
        "puzzle 3D ad incastro geometrico",
        "dadi da gioco poliedrici",
        "elementi di scenografia sci-fi e cyberpunk",
        "repliche di artefatti e props meccanici",
        "portalampade e basi per lampade da tavolo",
        "supporti per spazzolini elettrici da bagno",
        "portacoltelli magnetici da cucina",
        "scatole portaoggetti modulari impilabili",
        "cornici per foto con design geometrico",
        "posacenere da scrivania con incisioni",
        "portacavi e fermacavi da tavolo",
        "reggilibri con forme astratte o animali",
        "portamatite rotanti da scrivania",
        "reggette per organizzazione cavi elettrici",
        "supporti per tablet da parete",
        "portachiavi da muro personalizzabili",
        "distributori automatici per medicine",
        "portaocchiali da comodino",
        "scatole porta gioielli con scomparti",
        "supporti per smartphone da auto con ventosa",
        "portaorologio da comodino",
        "mini-vasi per piante grasse con drenaggio",
        "portapenne con base girevole",
        "ganci per parete con doppio attacco",
        "modelli architettonici in scala ridotta",
        "acchiappasogni parametrici 3D",
        "ingranaggi decorativi funzionanti",
        "lampade da scrivania LED stampate in 3D",
        "carte geografiche 3D con rilievo",
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
        "Sei un assistente tecnico esperto in modellazione 3D procedurale. "
        "Genera descrizioni brevi e dirette per script Python Blender 3.0."
    )
    user_prompt = (
        f"Genera una singola istruzione (massimo 2 frasi) per modellare un oggetto 3D della categoria '{category}'. "
        "Includi dimensioni specifiche (es. cm o mm) e dettagli fisici (es. spessore, cavita, raggio). "
        "Rispondi SOLO con l'istruzione, senza virgolette, senza convenevoli e senza codice. "
        "Inizia sempre con 'Crea un...' o 'Genera un...' e termina la frase con 'Blender 3.0.'."
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
                logger.warning("Errore LLM (%s): %s", response.status, text)
    except Exception as e:
        logger.warning("Eccezione chiamata LLM: %s", e)
    return f"Crea un oggetto di tipo {category} con dimensioni standard, pareti 2mm. Blender 3.0."


def _slug(text: str) -> str:
    words = re.sub(r"[^\w\s]", "", text.lower()).split()
    stop = {"crea", "un", "una", "con", "da", "per", "di", "in", "a", "blender", "30",
            "stampabile", "3d", "pareti", "altezza", "circa", "mm", "cm", "genera", "il", "la"}
    meaningful = [w for w in words if w not in stop and not w.isdigit()][:6]
    return "_".join(meaningful) if meaningful else "oggetto_generico"


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
        f"Data/Ora:        {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Cartella:        {session_dir}",
        f"Job eseguiti:    {n_total}",
        f"Riusciti:        {n_ok}",
        f"Falliti:         {n_fail}",
        f"Tasso successo:  {rate}",
        f"Tempo totale:    {_elapsed(total_elapsed)}",
        f"Media per job:   {_elapsed(total_elapsed / n_total) if n_total else 'N/A'}",
        "", "DETTAGLIO JOB", "-" * 60,
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
            error = "Script vuoto o non valido restituito dall'agente."
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
        logger.critical("OPENROUTER_API_KEY non trovata (per generazione prompt batch).")
        return
    if not nvidia_key:
        logger.warning("NVIDIA_API_KEY non trovata. La pipeline userà solo OPENROUTER_API_KEY tramite LLM_BASE_URL.")

    session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = Path(BATCH_CFG["output_dir"]) / f"session_{session_ts}"
    session_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Cartella sessione: %s", session_dir.resolve())

    blender_path = os.environ.get("BLENDER_PATH", "blender")
    runner = BlenderRunner(blender_executable=blender_path)
    db = VectorDB()
    try:
        await db.build()
    except Exception as e:
        logger.warning("VectorDB build fallito (%s). Continuo senza.", e)

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
    logger.info("Avvio ciclo. CTRL+C per interrompere.")

    try:
        while True:
            if max_jobs and idx > max_jobs:
                logger.info("Limite massimo job (%s) raggiunto.", max_jobs)
                break

            category = random.choice(BATCH_CFG["prompt_categories"])
            logger.info("Generazione prompt (Categoria: %s)...", category)
            prompt = await generate_dynamic_prompt(api_key, category)

            result = await run_single_job(orch, prompt, idx)
            results.append({k: v for k, v in result.items() if k != "script"})

            if result["success"] and result["script"]:
                job_dir = save_result(session_dir, idx, prompt, result["script"], True, result["phase_reached"], result["elapsed_sec"])
                n_ok += 1
                logger.info("Script salvato in: %s", job_dir)
            else:
                save_result(session_dir, idx, prompt, result["script"] or "# Nessuno script", False, result["phase_reached"], result["elapsed_sec"], result["error"])
                logger.warning("Job %d fallito: %s", idx, result['error'][:80] if result['error'] else 'Errore sconosciuto')
                if not BATCH_CFG.get("skip_failed", True):
                    logger.warning("skip_failed=False -- interruzione batch.")
                    break

            total_elapsed = time.time() - session_start
            logger.info("Avanzamento: Job %d | OK %d | FAIL %d | Trascorso: %s", idx, n_ok, idx - n_ok, _elapsed(total_elapsed))
            write_session_report(session_dir, results, total_elapsed)

            pause = BATCH_CFG.get("pause_between_jobs", 10)
            logger.info("Pausa %ds...", pause)
            await asyncio.sleep(pause)
            idx += 1

    except KeyboardInterrupt:
        logger.info("Interruzione utente. Salvataggio report...")

    global _HTTP_SESSION
    if _HTTP_SESSION is not None:
        await _HTTP_SESSION.close()
        _HTTP_SESSION = None

    total_elapsed = time.time() - session_start
    write_session_report(session_dir, results, total_elapsed)

    n_total = len(results)
    logger.info("=" * 50)
    logger.info("SESSIONE COMPLETATA")
    logger.info("=" * 50)
    logger.info("Job: %d | OK: %d | FAIL: %d | Rate: %s | Tempo: %s",
                n_total, n_ok, n_total - n_ok,
                f"{n_ok/n_total*100:.1f}%" if n_total else "N/A",
                _elapsed(total_elapsed))
    logger.info("Output: %s", session_dir.resolve())


if __name__ == "__main__":
    asyncio.run(main())
