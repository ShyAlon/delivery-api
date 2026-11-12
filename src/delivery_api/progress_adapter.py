"""One normalized task-event contract, fed by engine journal or generator JSONL."""
from __future__ import annotations
import logging
from vmodel_contracts.progress_signal import ProgressSignal
from .phases import PHASE_BY_KEY

log=logging.getLogger(__name__)
PREFIX="VMODEL_PROGRESS_JSONL:"


def ingest_generator_line(store, task_id: str, line: str, launched_phase: str, session_id: str | None = None) -> bool:
    if not line.startswith(PREFIX):return False
    try:
        signal=ProgressSignal.model_validate_json(line[len(PREFIX):])
        definition=PHASE_BY_KEY.get(signal.phase_key)
        if (signal.task_id!=task_id or (session_id is not None and signal.session_id!=session_id)
            or definition is None or definition.executor!="generator" or signal.phase_key!=launched_phase):
            raise ValueError("progress identity or phase mismatch")
        store.ingest_progress(signal,"generator")
    except Exception:
        log.warning("malformed or mismatched generator progress frame",exc_info=True)
        # A framed record is never reinterpreted as human console prose.
    return True


def retain_interview_signal(signal):
    # Elapsed time is a client clock, not durable state. Finished model calls
    # already have separate usage/log evidence, not another task-history copy.
    return signal.status == "failed" or (signal.status != "heartbeat" and not signal.stage.startswith("llm_call:"))


def validate_interview_page(task_id,session_id,page):
    previous=None;seen=set()
    for signal in page.events:
        definition=PHASE_BY_KEY.get(signal.phase_key)
        if (signal.task_id!=task_id or signal.session_id!=session_id or
            definition is None or definition.executor!="interview"):
            raise ValueError("interview progress identity or phase mismatch")
        if previous is not None and signal.cursor!=previous+1:
            raise ValueError("interview progress cursor gap")
        if signal.event_id in seen:raise ValueError("duplicate event ID in progress page")
        seen.add(signal.event_id)
        previous=signal.cursor
    if previous is not None and page.next_cursor!=previous:
        raise ValueError("interview progress page tail mismatch")


def ingest_interview_page(store,task_id: str,session_id: str,page):
    validate_interview_page(task_id,session_id,page)
    return store.ingest_interview_progress_page(task_id,session_id,page)


def ingest_answer_page(store,task_id: str,session_id: str,page):
    """Retain stage history, not the answer's transient cursor or per-call log."""
    validate_interview_page(task_id,session_id,page)
    if not any(retain_interview_signal(signal) for signal in page.events):return 0
    return store.ingest_answer_progress_page(task_id,session_id,page)
