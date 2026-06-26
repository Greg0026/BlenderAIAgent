import os
from pathlib import Path
from typing import Dict, Any, List

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_dotenv(path: Path = Path(".env")):
    if load_dotenv:
        load_dotenv(dotenv_path=path)
        return
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


_load_dotenv()


def _parse_comma_list(val: str) -> List[str]:
    return [item.strip() for item in val.split(",") if item.strip()]


def _try_env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is not None and val.strip():
        try:
            return int(val.strip())
        except ValueError:
            pass
    return default


REQUIRED_KEY_GROUPS = [
    ["NVIDIA_API_KEY"],
    ["OPENROUTER_API_KEY"],
    ["OPENCODE_ZEN_API_KEY"],
]


def validate_api_keys() -> None:
    has_any = any(
        all(os.environ.get(k) for k in group)
        for group in REQUIRED_KEY_GROUPS
    )
    if not has_any:
        raise EnvironmentError(
            "Nessuna API key trovata. Imposta almeno una tra: "
            "NVIDIA_API_KEY, OPENROUTER_API_KEY, OPENCODE_ZEN_API_KEY. "
            "Copia .env.example in .env e inserisci i valori."
        )


WEAK_MODEL_FRAGMENTS = [
    "1b", "1.3b", "2b", "3b", "3.2b",
    "7b", "8b",
]


_DEFAULT_BASE = (
    "https://opencode.ai/zen/v1"
    if os.environ.get("OPENCODE_ZEN_API_KEY")
    else "https://integrate.api.nvidia.com/v1"
)

CFG: Dict[str, Any] = {
    "base_url":        os.environ.get("LLM_BASE_URL", _DEFAULT_BASE),
    "model_id":        os.environ.get("LLM_MODEL_ID", "deepseek-v4-flash-free"),
    "vision_model":    os.environ.get("LLM_VISION_MODEL", "kimi-k2.6"),
    "fallback_models": _parse_comma_list(
        os.environ.get(
            "LLM_FALLBACK_MODELS",
            "qwen3.6-plus-free,minimax-m3-free,"
            "nemotron-3-ultra-free,big-pickle,"
            "mimo-v2.5-free,north-mini-code-free",
        )
    ),
    "weak_model_fragments": _parse_comma_list(
        os.environ.get("WEAK_MODEL_FRAGMENTS", ",".join(WEAK_MODEL_FRAGMENTS))
    ),

    "temperature":      float(os.environ.get("LLM_TEMPERATURE", "0.2")),
    "top_p":            float(os.environ.get("LLM_TOP_P", "0.7")),
    "max_tokens":       int(os.environ.get("LLM_MAX_TOKENS", "16384")),

    "blender_timeout":  int(os.environ.get("BLENDER_TIMEOUT", "120")),
    "error_loops":      int(os.environ.get("ERROR_LOOPS", "8")),
    "fix_loops":        int(os.environ.get("FIX_LOOPS", "6")),
    "phase_timeout":    int(os.environ.get("PHASE_TIMEOUT", "600")),
    "api_base_delay":      float(os.environ.get("API_BASE_DELAY", "3.0")),
    "api_max_retries":     int(os.environ.get("API_MAX_RETRIES", "3")),
    "api_inter_pause":     float(os.environ.get("API_INTER_PAUSE", "8.0")),
    "retry_backoff_s":     float(os.environ.get("RETRY_BACKOFF_S", "1.0")),
    "first_chunk_timeout": int(os.environ.get("FIRST_CHUNK_TIMEOUT", "30")),
    "min_code_length":  int(os.environ.get("MIN_CODE_LENGTH", "400")),
    "f2_max_tokens":    int(os.environ.get("F2_MAX_TOKENS", "40000")),

    "stl_output_dir":   os.environ.get("STL_OUTPUT_DIR", "~/Desktop/blender_prints"),

    "max_mesh_fix_attempts":      int(os.environ.get("MAX_MESH_FIX_ATTEMPTS", "3")),
    "max_fidelity_review_passes": int(os.environ.get("MAX_FIDELITY_REVIEW_PASSES", "3")),

    "vision_review_enabled": os.environ.get("VISION_REVIEW_ENABLED", "true").lower() == "true",
    "vision_render_dir":     os.environ.get("VISION_RENDER_DIR", None) or None,

    "batch_llm_model":  os.environ.get("BATCH_LLM_MODEL", "openai/gpt-oss-120b:free"),
    "batch_max_jobs":   _try_env_int("BATCH_MAX_JOBS", 0) or None,
    "batch_pause":      int(os.environ.get("BATCH_PAUSE", "10")),

    "static_max_len":   int(os.environ.get("STATIC_MAX_LEN", "40000")),
}
