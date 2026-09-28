"""Downstream spec-generator route defaults (not HTTP interview resolution).

The interview engine pins an explicit HTTP session map, seeded by legacy stage
keys only for new sessions without an explicit selection. This module
resolves current generator defaults and explicit downstream selections.
"""
from __future__ import annotations
import os

DEFAULT_OLLAMA_MODEL="phi4"
DEEPSEEK_MODEL="deepseek-chat"

def ollama_model()->str:
    return os.environ.get("DELIVERY_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

def route_value(route:str, model:str|None=None)->str:
    if route not in ("ollama","deepseek"):raise ValueError(f"unsupported model route: {route!r}")
    value=model if model is not None else (ollama_model() if route=="ollama" else DEEPSEEK_MODEL)
    if not value or any(ch.isspace() for ch in value) or value.startswith(":"):
        raise ValueError("model name must be a nonempty provider model identifier")
    return f"{route}:{value}"


def needs_initial_model()->str:
    """Current generator configuration, independent of the interview session.

    The subprocess reads shared <require-root>/.env and then repo-local .env,
    while a real environment variable wins. Mirror only the NEEDS setting.
    """
    from pathlib import Path
    repo=Path(os.environ.get("SPEC_GENERATOR_DIR") or Path(__file__).resolve().parents[3]/"spec-generator")
    config={}
    for path in (repo.parent/".env",repo/".env"):
        if path.is_file():
            for line in path.read_text().splitlines():
                key,sep,value=line.strip().partition("=")
                if sep and key=="SPEC_GENERATOR_MODEL_NEEDS":config[key]=value.strip().strip('"').strip("'")
    return os.environ.get("SPEC_GENERATOR_MODEL_NEEDS") or config.get("SPEC_GENERATOR_MODEL_NEEDS") or f"ollama:{DEFAULT_OLLAMA_MODEL}"
