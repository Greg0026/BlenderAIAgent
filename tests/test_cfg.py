"""Test per cfg.py — validazione API keys e parsing."""
import os
import pytest
from contextlib import contextmanager


@contextmanager
def _env_patch(vars_dict):
    old = {}
    for k, v in vars_dict.items():
        old[k] = os.environ.get(k)
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestValidateApiKeys:
    def test_raises_when_all_missing(self):
        with _env_patch({"NVIDIA_API_KEY": "", "OPENROUTER_API_KEY": "", "OPENCODE_ZEN_API_KEY": ""}):
            from cfg import validate_api_keys
            with pytest.raises(EnvironmentError):
                validate_api_keys()

    def test_passes_with_nvidia_only(self):
        with _env_patch({"NVIDIA_API_KEY": "nvapi-test", "OPENROUTER_API_KEY": "", "OPENCODE_ZEN_API_KEY": ""}):
            from cfg import validate_api_keys
            validate_api_keys()

    def test_passes_with_openrouter_only(self):
        with _env_patch({"NVIDIA_API_KEY": "", "OPENROUTER_API_KEY": "sk-or-test", "OPENCODE_ZEN_API_KEY": ""}):
            from cfg import validate_api_keys
            validate_api_keys()

    def test_passes_with_opencodezen_only(self):
        with _env_patch({"NVIDIA_API_KEY": "", "OPENROUTER_API_KEY": "", "OPENCODE_ZEN_API_KEY": "ocz-test"}):
            from cfg import validate_api_keys
            validate_api_keys()

    def test_passes_with_all_three(self):
        with _env_patch({"NVIDIA_API_KEY": "nvapi-test", "OPENROUTER_API_KEY": "sk-or-test", "OPENCODE_ZEN_API_KEY": "ocz-test"}):
            from cfg import validate_api_keys
            validate_api_keys()
