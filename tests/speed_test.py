"""Test di velocita per le API LLM configurate in .env.

Usage:
    python -m tests.speed_test
    python -m tests.speed_test --model <model_id>
    python -m tests.speed_test --provider nvidia|openrouter|opencodezen
"""

import asyncio
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from cfg import CFG


PROMPT_SIMPLE = "Return only 'OK' and nothing else."
PROMPT_MEDIUM = """Write a Python function that calculates the fibonacci sequence up to n.
Return only the code, no explanation."""

PROMPT_HEAVY = """Write a complete Python class for a 3D renderer using Blender's bpy module.
Include materials, lighting, camera setup, and a render function.
The class should support configurable resolution and output format.
Return only the code."""


async def test_model(client: AsyncOpenAI, model: str, prompt: str, label: str, timeout: int) -> dict:
    result = {
        "model": model,
        "prompt": label,
        "first_token_s": None,
        "total_s": None,
        "chars": 0,
        "error": None,
    }

    messages = [{"role": "user", "content": prompt}]

    try:
        start = time.monotonic()
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=512,
            stream=True,
        )

        first_token = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
        first_token_time = time.monotonic()
        result["first_token_s"] = round(first_token_time - start, 2)

        content = first_token.choices[0].delta.content or ""
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            content += delta

        end = time.monotonic()
        result["total_s"] = round(end - start, 2)
        result["chars"] = len(content)
        result["first_token_s"] = round(first_token_time - start, 2)
    except asyncio.TimeoutError:
        result["error"] = f"timeout {timeout}s"
    except Exception as e:
        result["error"] = str(e)[:120]

    return result


def _detect_provider(base_url: str) -> str:
    url = base_url.lower()
    if "nvidia" in url:
        return "nvidia"
    if "openrouter" in url:
        return "openrouter"
    if "opencode" in url or "zen" in url:
        return "opencodezen"
    return "other"


def _resolve_api_key(provider: str) -> str:
    key_map = {
        "nvidia": "NVIDIA_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "opencodezen": "OPENCODE_ZEN_API_KEY",
    }
    key = os.environ.get(key_map.get(provider, ""))
    if key:
        return key
    for k in ("OPENCODE_ZEN_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        v = os.environ.get(k)
        if v:
            return v
    return ""


def print_table(rows: list[dict]):
    header = f"{'Modello':<40} {'Prompt':<12} {'TTFT(s)':<10} {'Totale(s)':<12} {'Chars':<8} {'Errore'}"
    sep = "-" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)
    for r in rows:
        ttft = f"{r['first_token_s']:.2f}" if r["first_token_s"] is not None else "-"
        tot = f"{r['total_s']:.2f}" if r["total_s"] is not None else "-"
        chars = str(r["chars"]) if r["chars"] else "-"
        err = r["error"] or ""
        print(f"{r['model']:<40} {r['prompt']:<12} {ttft:<10} {tot:<12} {chars:<8} {err}")
    print(sep)


async def main():
    parser = argparse.ArgumentParser(description="Speed test API LLM")
    parser.add_argument("--model", help="Test solo un modello specifico")
    parser.add_argument("--provider", choices=["nvidia", "openrouter", "opencodezen"],
                        help="Test solo un provider specifico")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout primo token (default: 120)")
    args = parser.parse_args()

    base_url = CFG["base_url"]
    provider = _detect_provider(base_url)
    api_key = _resolve_api_key(provider)

    print(f"\nConfigurazione letta da .env:")
    print(f"  Base URL:  {base_url}")
    print(f"  Provider:  {provider}")
    print(f"  API Key:   {api_key[:12]}...{api_key[-4:]}" if len(api_key) > 16 else "  API Key:   [non trovata]")
    print(f"  Modello principale: {CFG['model_id']}")
    print(f"  Fallback: {CFG['fallback_models']}")
    print(f"  First chunk timeout cfg: {CFG.get('first_chunk_timeout', 30)}s")
    print(f"  Phase timeout cfg: {CFG.get('phase_timeout', 300)}s")

    if not api_key:
        print("\nERRORE: Nessuna API key configurata.")
        sys.exit(1)

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=None)

    models_to_test = []
    if args.model:
        models_to_test.append(args.model)
    else:
        models_to_test.append(CFG["model_id"])
        for m in CFG["fallback_models"]:
            models_to_test.append(m)

    prompts = [
        ("semplice", PROMPT_SIMPLE),
        ("medio", PROMPT_MEDIUM),
        ("pesante", PROMPT_HEAVY),
    ]

    all_results = []

    for model in models_to_test:
        for p_name, p_text in prompts:
            print(f"\n--- {model} / {p_name} ---")
            result = await test_model(client, model, p_text, p_name, args.timeout)
            all_results.append(result)
            if result["first_token_s"] is not None:
                print(f"  Primo token: {result['first_token_s']}s")
                print(f"  Totale:      {result['total_s']}s")
                print(f"  Caratteri:   {result['chars']}")
            else:
                print(f"  ERRORE: {result['error']}")

    print_table(all_results)

    totals = [r for r in all_results if r["error"] is None]
    if totals:
        avg_ttft = sum(r["first_token_s"] for r in totals) / len(totals)
        avg_total = sum(r["total_s"] for r in totals) / len(totals)
        print(f"\nMedie (richieste riuscite):")
        print(f"  TTFT medio:  {avg_ttft:.2f}s")
        print(f"  Totale medio: {avg_total:.2f}s")
        print(f"  Riuscite: {len(totals)}/{len(all_results)}")


if __name__ == "__main__":
    asyncio.run(main())
