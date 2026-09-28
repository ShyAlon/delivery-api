"""Downstream spec-generator route defaults (not HTTP interview resolution).

The interview engine owns its stage env and pins the effective HTTP model at
Start; delivery fetches that map from its route-preview endpoint. This module
still maps task route labels to downstream spec-generator model values.
"""
from __future__ import annotations
import os

DEFAULT_OLLAMA_MODEL="qwen2.5-coder:14b"
DEEPSEEK_MODEL="deepseek-chat"

def ollama_model()->str:
    return os.environ.get("DELIVERY_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

def route_value(route:str)->str:
    if route=="ollama":return f"ollama:{ollama_model()}"
    if route=="deepseek":return f"deepseek:{DEEPSEEK_MODEL}"
    raise ValueError(f"unsupported model route: {route!r}")
