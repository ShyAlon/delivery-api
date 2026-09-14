"""Durable task, phase, event and artifact state backed by SQLite."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from vmodel_contracts import RunState

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
  token TEXT PRIMARY KEY, run_id TEXT NOT NULL, founder_label TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, state TEXT NOT NULL, submission TEXT, session_ref TEXT,
  cost_usd REAL NOT NULL DEFAULT 0, budget_usd REAL NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, record TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  run_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, title TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, active_phase TEXT, model_route TEXT, generation INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS phases (
  task_id TEXT NOT NULL, phase_key TEXT NOT NULL, label TEXT NOT NULL, position INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
  completed INTEGER NOT NULL DEFAULT 0, total INTEGER,
  started_at REAL, completed_at REAL, PRIMARY KEY(task_id, phase_key)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, phase_key TEXT,
  kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
CREATE TABLE IF NOT EXISTS task_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, generation INTEGER NOT NULL,
  phase_key TEXT NOT NULL, name TEXT NOT NULL, ref TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'application/json',
  stale INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
"""

PHASES = (
    ("intent", "Intent model"),
    ("conops", "Concept of operations"),
    ("needs", "Stakeholder needs"),
    ("srs", "Requirements"),
    ("architecture", "Architecture"),
    ("specs", "Item specifications"),
    ("tests", "Verification"),
)


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def _commit(self):
        self.db.commit()

    # Legacy link/run API, kept while the task UI replaces the tester flow.
    def create_link(self, founder_label: str) -> dict:
        with self._lock:
            token, run_id, now = secrets.token_urlsafe(24), secrets.token_hex(8), time.time()
            self.db.execute("INSERT INTO links VALUES (?,?,?,1,?)", (token, run_id, founder_label, now))
            self.db.execute("INSERT INTO runs (run_id,state,budget_usd,created_at,updated_at) VALUES (?,?,?,?,?)",
                            (run_id, RunState.CREATED.value, 6.0, now, now))
            self._commit()
            return {"token": token, "run_id": run_id}

    def link(self, token: str) -> dict | None:
        row = self.db.execute("SELECT * FROM links WHERE token=? AND active=1", (token,)).fetchone()
        return dict(row) if row else None

    def deactivate_link(self, token: str) -> None:
        with self._lock:
            self.db.execute("UPDATE links SET active=0 WHERE token=?", (token,)); self._commit()

    def set_state(self, run_id: str, state: RunState) -> None:
        with self._lock:
            self.db.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?", (state.value, time.time(), run_id)); self._commit()

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def add_usage(self, run_id: str, record_json: str, cost_usd: float) -> float:
        with self._lock:
            self.db.execute("INSERT INTO usage (run_id,record,ts) VALUES (?,?,?)", (run_id, record_json, time.time()))
            self.db.execute("UPDATE runs SET cost_usd=cost_usd+?, updated_at=? WHERE run_id=?", (cost_usd, time.time(), run_id))
            self._commit()
            return self.run(run_id)["cost_usd"]

    def total_spend(self) -> float:
        return self.db.execute("SELECT COALESCE(SUM(cost_usd),0) t FROM runs").fetchone()["t"]

    def add_artifact(self, run_id: str, kind: str, ref: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (run_id, kind, ref, time.time())); self._commit()

    def artifacts(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM artifacts WHERE run_id=?", (run_id,))]

    # UI task model.
    def create_task(self, title: str, brief: str = "", *, task_id: str | None = None) -> dict:
        with self._lock:
            task_id, now = task_id or secrets.token_hex(6), time.time()
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                            (task_id, title, brief, "not_started", None, None, 1, now, now))
            self.db.executemany(
                "INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (?,?,?,?, 'pending')",
                [(task_id, key, label, i) for i, (key, label) in enumerate(PHASES)],
            )
            self._commit()
            return self.task(task_id)

    def list_tasks(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [self._task_record(r) for r in rows]

    def task(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._task_record(row) if row else None

    def _task_record(self, row) -> dict:
        item = dict(row)
        item["phases"] = [dict(r) for r in self.db.execute(
            "SELECT phase_key,label,position,state,detail,completed,total,started_at,completed_at "
            "FROM phases WHERE task_id=? ORDER BY position", (item["task_id"],))]
        item["artifact_count"] = self.db.execute(
            "SELECT count(*) n FROM task_artifacts WHERE task_id=? AND stale=0", (item["task_id"],)).fetchone()["n"]
        return item

    def update_phase(self, task_id: str, phase_key: str, state: str, *, detail: str = "",
                     completed: int = 0, total: int | None = None) -> None:
        now = time.time()
        with self._lock:
            current = self.db.execute("SELECT state,started_at FROM phases WHERE task_id=? AND phase_key=?",
                                      (task_id, phase_key)).fetchone()
            if not current:
                raise KeyError(phase_key)
            started = current["started_at"] or (now if state == "running" else None)
            ended = now if state == "done" else None
            self.db.execute(
                "UPDATE phases SET state=?,detail=?,completed=?,total=?,started_at=?,completed_at=? "
                "WHERE task_id=? AND phase_key=?",
                (state, detail, completed, total, started, ended, task_id, phase_key),
            )
            task_state = "running" if state == "running" else ("failed" if state == "failed" else None)
            if state == "done" and phase_key == PHASES[-1][0]: task_state = "done"
            if task_state:
                self.db.execute("UPDATE tasks SET state=?,active_phase=?,updated_at=? WHERE task_id=?",
                                (task_state, phase_key, now, task_id))
            else:
                self.db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (now, task_id))
            self._commit()

    def set_model_route(self, task_id: str, model_route: str) -> None:
        with self._lock:
            self.db.execute("UPDATE tasks SET model_route=?,updated_at=? WHERE task_id=?",
                            (model_route, time.time(), task_id)); self._commit()

    def add_event(self, task_id: str, kind: str, message: str, *, phase_key: str | None = None,
                  payload: dict | None = None) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                                  (task_id, phase_key, kind, message, json.dumps(payload or {}), time.time()))
            self.db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (time.time(), task_id)); self._commit()
            return cur.lastrowid

    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                               (task_id, after, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["payload"] = json.loads(item["payload"]); result.append(item)
        return result

    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str,
                          mime_type: str = "application/json") -> None:
        task = self.task(task_id)
        with self._lock:
            self.db.execute(
                "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at) VALUES (?,?,?,?,?,?,?)",
                (task_id, task["generation"], phase_key, name, ref, mime_type, time.time()),
            ); self._commit()

    def task_artifacts(self, task_id: str, *, include_stale: bool = True) -> list[dict]:
        where = "task_id=?" if include_stale else "task_id=? AND stale=0"
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM task_artifacts WHERE {where} ORDER BY generation DESC,id", (task_id,))]

    def prepare_rerun(self, task_id: str, from_phase: str, policy: str) -> int:
        """The one rerun-policy seam. Only archive is active until the founder decides."""
        if policy != "archive":
            raise ValueError("rerun policy is not decided; only the reversible archive preview is available")
        positions = dict((k, i) for i, (k, _) in enumerate(PHASES))
        if from_phase not in positions: raise KeyError(from_phase)
        with self._lock:
            downstream = [k for k, i in positions.items() if i >= positions[from_phase]]
            marks = ",".join("?" for _ in downstream)
            self.db.execute(f"UPDATE task_artifacts SET stale=1 WHERE task_id=? AND phase_key IN ({marks})",
                            (task_id, *downstream))
            self.db.execute(f"UPDATE phases SET state='pending',detail='',completed=0,total=NULL,started_at=NULL,completed_at=NULL "
                            f"WHERE task_id=? AND phase_key IN ({marks})", (task_id, *downstream))
            self.db.execute("UPDATE tasks SET generation=generation+1,state='not_started',active_phase=NULL,updated_at=? WHERE task_id=?",
                            (time.time(), task_id)); self._commit()
            return self.task(task_id)["generation"]
