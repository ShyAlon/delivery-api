import os
os.environ["DELIVERY_DB_PATH"] = ":memory:"

import json


def test_route_value_default_and_env_override(monkeypatch):
    from delivery_api import model_routes
    monkeypatch.delenv("DELIVERY_OLLAMA_MODEL", raising=False)
    assert model_routes.route_value("ollama") == "ollama:qwen2.5-coder:14b"
    assert model_routes.route_value("deepseek") == "deepseek:deepseek-chat"
    monkeypatch.setenv("DELIVERY_OLLAMA_MODEL", "phi4")
    assert model_routes.route_value("ollama") == "ollama:phi4"


def test_interview_route_env_uses_override(monkeypatch):
    from delivery_api import app as module
    monkeypatch.setenv("DELIVERY_OLLAMA_MODEL", "phi4")
    env = module._interview_route_env("no-such-task")
    assert set(env) == {"INTERVIEW_ENGINE_MODEL_PROBING", "INTERVIEW_ENGINE_MODEL_DISTILLATION",
                        "INTERVIEW_ENGINE_MODEL_PHRASING", "INTERVIEW_ENGINE_MODEL_SCOPE_TIEBREAK",
                        "INTERVIEW_ENGINE_MODEL_EXPERT_OPINION"}
    assert set(env.values()) == {"ollama:phi4"}


def _seed_task(module, store, task_id):
    if store.task(task_id):
        store.delete_task(task_id)
    store.create_task("Provenance", "An idea", task_id=task_id)
    store.add_event(task_id, "waiting_input", "Interviewer: Q1?", phase_key="intent",
                    payload={"question": "Q1?"})
    store.add_event(task_id, "answer", "You: A1", phase_key="intent",
                    payload={"answer": "A1", "transcript": True})


def test_transcript_header_from_usage_jsonl(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-usage-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-prov")
    usage = tmp_path / "s-prov" / "interview" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    records = [
        {"session_id": "s-prov", "stage": "probing", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
        {"session_id": "s-prov", "stage": "distillation", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
    ]
    usage.write_text("".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))
    text = module._interview_transcript_markdown(task_id)
    assert text.splitlines()[0].startswith("Model: ollama:phi4")
    assert "probing" in text.splitlines()[0] and "distillation" in text.splitlines()[0]
    assert "## Interview questions and answers" in text
    store.delete_task(task_id)


def test_transcript_header_mixed_models_lists_stages(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-mixed-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-mixed")
    usage = tmp_path / "s-mixed" / "interview" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    records = [
        {"session_id": "s-mixed", "stage": "probing", "provider": "ollama", "model": "phi4",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
        {"session_id": "s-mixed", "stage": "distillation", "provider": "ollama", "model": "qwen2.5-coder:14b",
         "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1, "cost_usd": 0.0},
    ]
    usage.write_text("".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))
    lines = module._interview_transcript_markdown(task_id).splitlines()
    assert "Model: distillation = ollama:qwen2.5-coder:14b" in lines
    assert "Model: probing = ollama:phi4" in lines
    store.delete_task(task_id)


def test_transcript_header_falls_back_to_resolved_route_event(monkeypatch, tmp_path):
    from delivery_api import app as module
    store = module.store
    task_id = "provenance-fallback-test"
    _seed_task(module, store, task_id)
    store.set_engine_session(task_id, "s-missing-artifacts")
    store.add_event(task_id, "model_route", "interview uses ollama:phi4", phase_key="intent",
                    payload={"route": "ollama", "resolved": "ollama:phi4", "model": "phi4", "scope": "start"})
    monkeypatch.setenv("VMODEL_ARTIFACTS_DIR", str(tmp_path))  # no usage.jsonl on disk
    text = module._interview_transcript_markdown(task_id)
    assert text.splitlines()[0] == "Model: ollama:phi4"
    store.delete_task(task_id)
