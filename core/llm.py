import asyncio
import os
import pathlib
from typing import Any, Dict, List, Optional

import openai
from openai import AsyncOpenAI, APIError

from cfg import CFG
from log import logger
from utils.code import extract_code


_PROMPT_CACHE: Dict[str, str] = {}
_PROMPT_CACHE_MAX = 32


def get_prompt(name: str) -> str:
    if name not in _PROMPT_CACHE:
        if len(_PROMPT_CACHE) >= _PROMPT_CACHE_MAX:
            _PROMPT_CACHE.clear()
        p = pathlib.Path(__file__).parent.parent / "prompts" / name
        if p.exists():
            _PROMPT_CACHE[name] = p.read_text(encoding="utf-8")
        else:
            logger.warning("Prompt file not found: %s", p)
            _PROMPT_CACHE[name] = ""
    return _PROMPT_CACHE[name]


def _detect_provider(base_url: str) -> str:
    url_lower = base_url.lower()
    if "nvidia" in url_lower:
        return "nvidia"
    if "openrouter" in url_lower:
        return "openrouter"
    if "opencode" in url_lower or "zen" in url_lower:
        return "opencodezen"
    return "other"


def _resolve_api_key(provider: str) -> str:
    if provider == "nvidia":
        return os.environ.get("NVIDIA_API_KEY") or ""
    if provider == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY") or ""
    if provider == "opencodezen":
        return os.environ.get("OPENCODE_ZEN_API_KEY") or ""
    return (os.environ.get("OPENCODE_ZEN_API_KEY")
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or "")


class LLMClient:
    _MODEL_PROVIDER_MAP: Dict[str, str] = {
        "nvidia": "nvidia",
        "openai": "other",
    }

    def __init__(self):
        base_url = CFG.get("base_url", "https://integrate.api.nvidia.com/v1")
        self.provider = _detect_provider(base_url)
        self.api_key = _resolve_api_key(self.provider)

        if not self.api_key:
            self.api_key = (os.environ.get("OPENCODE_ZEN_API_KEY")
                            or os.environ.get("NVIDIA_API_KEY")
                            or os.environ.get("OPENROUTER_API_KEY")
                            or "")

        if not self.api_key:
            raise EnvironmentError(
                "No API key found. Set OPENCODE_ZEN_API_KEY, NVIDIA_API_KEY or OPENROUTER_API_KEY in .env"
            )

        self.client = AsyncOpenAI(base_url=base_url, api_key=self.api_key, timeout=None)

        self.fallback_models = self._filter_weak_models(CFG.get("fallback_models", []))
        self.base_model = CFG.get("model_id", "")
        if not self.base_model:
            raise EnvironmentError("CFG['model_id'] not configured.")

        self._overall_timeout = CFG.get("phase_timeout", 300)

    @staticmethod
    def _filter_weak_models(models: List[str]) -> List[str]:
        fragments = [f.lower() for f in CFG.get("weak_model_fragments", [])]
        filtered = []
        for m in models:
            name_lower = m.lower()
            if any(f in name_lower for f in fragments):
                logger.debug("Removed weak model: %s", m)
                continue
            filtered.append(m)
        return filtered

    @staticmethod
    def _llm_extra_body() -> dict:
        base = CFG.get("base_url", "").lower()
        if "nvidia" in base:
            return {"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}}
        return {}

    async def _try_model(
        self,
        model: str,
        messages: List[Dict],
        temperature: float,
        max_tokens: int,
        top_p: float,
        do_extract: bool,
        min_len: int,
        label: str,
    ) -> Optional[str]:
        short = model.split("/")[-1]
        logger.info("[%s] >> calling %s ...", label, short)
        try:
            async def _stream_and_collect():
                stream = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    stream=True,
                    extra_body=self._llm_extra_body(),
                    extra_headers={"X-Title": "BlenderAIAgent-v5"},
                )
                ft = CFG.get("first_chunk_timeout", 30)
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=ft
                    )
                except asyncio.TimeoutError:
                    logger.warning("[%s] no first token from %s in %ds.", label, short, ft)
                    return None, None

                actual = getattr(chunk, "model", None) or model
                logger.info("[%s] << first token (%s)", label, actual.split("/")[-1])

                content = chunk.choices[0].delta.content or ""
                chunk_cnt = 1
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    content += delta
                    chunk_cnt += 1
                    if chunk_cnt % 100 == 0:
                        logger.info("[%s] ... %d chars (%s)", label, len(content), short)
                return content, model

            raw, actual_model = await asyncio.wait_for(
                _stream_and_collect(),
                timeout=self._overall_timeout,
            )
            if raw is None:
                return None

            if not raw.strip():
                logger.warning("[%s] Empty response from %s.", label, short)
                return None

            result = extract_code(raw) if do_extract else raw

            if not result or not result.strip():
                logger.warning("[%s] Content extraction failed from %s.", label, short)
                return None

            if do_extract and len(result.strip()) < min_len:
                logger.warning("[%s] Script too short (%d chars) from %s.", label, len(result.strip()), short)
                return None

            if label:
                logger.info("[%s] %d chars (%s)", label, len(result), actual_model.split("/")[-1])
            return result

        except asyncio.TimeoutError:
            logger.warning("[%s] Timeout %ds on %s.", label, self._overall_timeout, short)
            return None
        except (openai.OpenAIError, APIError) as e:
            logger.warning("[%s] API error with %s: %s", label, short, e)
            return None
        except Exception as e:
            logger.warning("[%s] Generic error with %s: %s", label, short, e)
            return None

    async def call(
        self,
        system: str,
        messages: List[Dict],
        label: str = "",
        expect_json: bool = False,
        do_extract_code: bool = True,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        full: List[Dict] = [{"role": "system", "content": system}]
        for m in messages:
            role = "assistant" if m["role"] in ("assistant", "model") else "user"
            full.append({"role": role, "content": m["content"]})

        _tokens = max_tokens if max_tokens is not None else CFG.get("max_tokens", 16384)
        _temp = temperature if temperature is not None else CFG.get("temperature", 0.15)
        min_len = CFG.get("min_code_length", 200)
        backoff_s = CFG.get("retry_backoff_s", 1.0)
        do_extract = (not expect_json) and do_extract_code
        top_p = CFG.get("top_p", 0.7)

        models = [self.base_model] + self.fallback_models

        for idx, model in enumerate(models):
            if idx > 0:
                logger.info("[%s] fallback to %s in %ds ...", label, model.split("/")[-1], backoff_s)
                await asyncio.sleep(backoff_s)
            result = await self._try_model(
                model, full, _temp, _tokens, top_p, do_extract, min_len, label
            )
            if result is not None:
                return result

        raise RuntimeError(
            f"[{label}] COLLAPSE: all cascade models failed."
        )
