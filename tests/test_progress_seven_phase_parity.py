"""Seven-phase progress/control parity through the real source adapters and gates."""
import asyncio
import sys
from uuid import uuid4

from delivery_api.control_snapshot import project
from delivery_api.phases import PHASE_DEFINITIONS
from delivery_api.progress_adapter import ingest_interview_page, PREFIX
from delivery_api.runner import ProcessRunner
from delivery_api.store import SQLiteStore
from vmodel_contracts.progress_signal import ProgressPage, ProgressSignal


def test_all_seven_phases_share_progress_and_gate_boundary(tmp_path):
    store = SQLiteStore(str(tmp_path / "parity.db"))
    task_id = store.create_task("Seven-phase parity")["task_id"]
    session_id = str(uuid4())
    cursor = 0
    runner = ProcessRunner(store)
    for phase in PHASE_DEFINITIONS:
        assert len(store.task(task_id)["phases"]) == 8
        if phase.executor == "interview":
            store.update_phase(task_id, phase.key, "running")
            cursor += 1
            signal = ProgressSignal(event_id=str(uuid4()), cursor=cursor,
                task_id=task_id, session_id=session_id, phase_key=phase.key,
                stage="configured-step", label=f"Working on {phase.label}", status="started")
            ingest_interview_page(store, task_id, session_id,
                ProgressPage(events=[signal], next_cursor=cursor))
            assert project(store.task(task_id), {"process_live": True}).mode == "working"
            # Source emits again on replay; delivery's cursor must not duplicate it.
            ingest_interview_page(store, task_id, session_id,
                ProgressPage(events=[signal], next_cursor=cursor))
            store.update_phase(task_id, phase.key, "awaiting_approval")
            gate = store.wait_at_gate(task_id, phase.key)
        else:
            out = tmp_path / phase.key
            out.mkdir()
            artifact = phase.artifacts[0]
            progress = ProgressSignal(event_id=str(uuid4()), cursor=1,
                task_id=task_id, session_id=session_id, phase_key=phase.key,
                stage="configured-step", label=f"Working on {phase.label}", status="started")
            payload = ('[]' if phase.key == 'tests'
                       else '{"session_id":"s","items":[]}' if phase.key == 'validation' else '{}')
            script = ("from pathlib import Path;import sys;"
                "out=Path(sys.argv[sys.argv.index('--out')+1]);"
                f"(out/{artifact!r}).write_text({payload!r});"
                f"print({(PREFIX + progress.model_dump_json())!r},flush=True)")
            assert asyncio.run(runner.run(task_id,
                [sys.executable, "-c", script, "--out", str(out)],
                cwd=tmp_path, phase=phase.key, infer_phase=False,
                env={"VMODEL_PROGRESS_TASK_ID": task_id,
                     "VMODEL_PROGRESS_SESSION_ID": session_id})) == 0
            gate = next(g for g in store.task(task_id)["gates"]
                        if g["after_phase"] == phase.key)
        task = store.task(task_id)
        assert task["state"] == "waiting_gate", phase.key
        assert gate["state"] == "waiting", phase.key
        snapshot = project(task, {"process_live": False}, artifacts=[{
            "phase_key": phase.key, "ref": str(tmp_path / phase.artifacts[0]),
            "present": True, "valid": True}])
        assert snapshot.mode == "waiting_gate", phase.key
        assert snapshot.phase_key == phase.key and snapshot.owner == "delivery"
        assert snapshot.wait_id == gate["gate_key"]
        assert snapshot.precondition["gate_revision"] == gate["revision"]
        events = [e for e in store.events(task_id) if e["kind"] == "progress"
                  and e["phase_key"] == phase.key and e["payload"].get("stage") == "configured-step"]
        assert len(events) == 1, phase.key
        assert events[0]["payload"]["source"] == phase.executor
        store.decide_gate(task_id, gate["gate_key"], "approve",
                          selected_option=f"Approve {phase.label}",
                          expected_revision=snapshot.precondition["gate_revision"])
        assert next(p for p in store.task(task_id)["phases"]
                    if p["phase_key"] == phase.key)["state"] == "approved"
    assert store.task(task_id)["state"] == "done"
    assert project(store.task(task_id), {}).mode == "completed"
