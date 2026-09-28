"""Model route resolution - one source of truth for route -> provider:model.

The ollama route's model is overridable per process via DELIVERY_OLLAMA_MODEL
(e.g. DELIVERY_OLLAMA_MODEL=phi4 scripts/run-ui.sh). The default is the standing
qwen2.5-coder:14b ruling (interview-engine e996b25); keep both call sites on
this module so the UI route cannot drift from it again.
"""
from __future__ import annotations
import os

DEFAULT_OLLAMA_MODEL="qwen2.5-coder:14b"
DEEPSEEK_MODEL="deepseek-chat"
INTERVIEW_DEEPSEEK_MODEL="deepseek-flash"

def ollama_model()->str:
    return os.environ.get("DELIVERY_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

def route_value(route:str)->str:
    if route=="ollama":return f"ollama:{ollama_model()}"
    if route=="deepseek":return f"deepseek:{DEEPSEEK_MODEL}"
    raise ValueError(f"unsupported model route: {route!r}")


def interview_route_value(route:str)->str:
    """The HTTP interview uses Flash; spec generation has a separate route."""
    if route=="deepseek":return f"deepseek:{INTERVIEW_DEEPSEEK_MODEL}"
    return route_value(route)
