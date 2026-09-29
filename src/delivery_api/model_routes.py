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


def phase_initial_model(stage:str)->str:
    """Current generator configuration, independent of the interview session.

    The subprocess reads shared <require-root>/.env and then repo-local .env,
    while a real environment variable wins. Mirror the configured setting for this generator stage.
    """
    from pathlib import Path
    repo=Path(os.environ.get("SPEC_GENERATOR_DIR") or Path(__file__).resolve().parents[3]/"spec-generator")
    key=f"SPEC_GENERATOR_MODEL_{stage.upper()}"
    config={}
    for path in (repo.parent/".env",repo/".env"):
        if path.is_file():
            for line in path.read_text().splitlines():
                name,sep,value=line.strip().partition("=")
                if sep and name==key:config[key]=value.strip().strip('"').strip("'")
    # Match spec_generator.model_config._STAGE_DEFAULTS when no stage override exists.
    return os.environ.get(key) or config.get(key) or "ollama:qwen2.5-coder:14b"


def needs_initial_model()->str:
    return phase_initial_model("needs")


def task_default_model(store, task_id: str, stage: str) -> tuple[str, str]:
    """The exact sticky selection, with an explicit legacy fallback source.

    Old task databases may predate model_default. Use only founder selections,
    never a later generator execution event which could have used an old per-stage
    environment default. No valid selection means a labeled configured fallback.
    """
    task = store.task(task_id)
    if not task: raise ValueError("task not found")
    if task.get("model_default"):
        return task["model_default"], "selected"
    # New Starts persist an exact value. This bounded scan is migration-only
    # for older tasks; if their selection has fallen out of the snapshot,
    # require an explicit choice instead of silently using an env default.
    events=store.events(task_id)
    for event in reversed(events):
        if event.get("kind") != "model_route": continue
        payload = event.get("payload") or {}
        if payload.get("scope") in ("start", "switch", "phase_override") or payload.get("source") == "explicit_retry_choice":
            resolved = payload.get("resolved")
            if resolved and ":" in resolved:
                return resolved, "previous selection"
    if task.get("engine_session_id"):
        raise ValueError("exact model selection is not recorded for this session; choose a model before continuing")
    return phase_initial_model(stage), "legacy generator configuration (no recorded exact selection)"
