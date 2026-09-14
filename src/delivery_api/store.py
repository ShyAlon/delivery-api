"""Durable task, phase, event and artifact state backed by SQLite."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

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
  completed INTEGER NOT NULL DEFAULT 0, total INTEGER, gate_basis_changed INTEGER NOT NULL DEFAULT 0,
  started_at REAL, completed_at REAL, PRIMARY KEY(task_id, phase_key)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, phase_key TEXT,
  kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
  task_id TEXT, detail TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id, id);
CREATE TABLE IF NOT EXISTS task_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, generation INTEGER NOT NULL,
  phase_key TEXT NOT NULL, name TEXT NOT NULL, ref TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'application/json',
  stale INTEGER NOT NULL DEFAULT 0, stale_reason TEXT, created_at REAL NOT NULL
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


@runtime_checkable
class Storage(Protocol):
    """Persistence boundary consumed by API and job control.

    A Postgres adapter implements this contract and is selected by the factory;
    routes and runners never see a connection or SQL dialect.
    """
    def create_link(self, founder_label: str) -> dict: ...
    def link(self, token: str) -> dict | None: ...
    def deactivate_link(self, token: str) -> None: ...
    def set_state(self, run_id: str, state: RunState) -> None: ...
    def set_submission(self, run_id: str, submission: str) -> None: ...
    def run(self, run_id: str) -> dict | None: ...
    def add_usage(self, run_id: str, record_json: str, cost_usd: float) -> float: ...
    def total_spend(self) -> float: ...
    def add_artifact(self, run_id: str, kind: str, ref: str) -> None: ...
    def artifacts(self, run_id: str) -> list[dict]: ...
    def create_task(self, title: str, brief: str = "", *, task_id: str | None = None) -> dict: ...
    def list_tasks(self) -> list[dict]: ...
    def task(self, task_id: str) -> dict | None: ...
    def update_phase(self, task_id: str, phase_key: str, state: str, **progress) -> None: ...
    def set_model_route(self, task_id: str, model_route: str) -> None: ...
    def add_event(self, task_id: str, kind: str, message: str, **context) -> int: ...
    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]: ...
    def audit(self, actor: str, action: str, task_id: str | None = None, detail: dict | None = None) -> int: ...
    def audit_entries(self, task_id: str | None = None, limit: int = 500) -> list[dict]: ...
    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str, mime_type: str = "application/json") -> None: ...
    def task_artifacts(self, task_id: str, *, include_stale: bool = True) -> list[dict]: ...
    def prepare_rerun(self, task_id: str, from_phase: str, policy: str = "archive", actor: str = "local user") -> int: ...


class SQLiteStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.executescript(SCHEMA)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Small additive migrations keep local task history readable."""
        phase_cols = {r[1] for r in self._db.execute("PRAGMA table_info(phases)")}
        artifact_cols = {r[1] for r in self._db.execute("PRAGMA table_info(task_artifacts)")}
        if "gate_basis_changed" not in phase_cols:
            self._db.execute("ALTER TABLE phases ADD COLUMN gate_basis_changed INTEGER NOT NULL DEFAULT 0")
        if "stale_reason" not in artifact_cols:
            self._db.execute("ALTER TABLE task_artifacts ADD COLUMN stale_reason TEXT")
        self._db.commit()

    def _commit(self):
        self._db.commit()

    # Legacy link/run API, kept while the task UI replaces the tester flow.
    def create_link(self, founder_label: str) -> dict:
        with self._lock:
            token, run_id, now = secrets.token_urlsafe(24), secrets.token_hex(8), time.time()
            self._db.execute("INSERT INTO links VALUES (?,?,?,1,?)", (token, run_id, founder_label, now))
            self._db.execute("INSERT INTO runs (run_id,state,budget_usd,created_at,updated_at) VALUES (?,?,?,?,?)",
                            (run_id, RunState.CREATED.value, 6.0, now, now))
            self._commit()
            return {"token": token, "run_id": run_id}

    def link(self, token: str) -> dict | None:
        row = self._db.execute("SELECT * FROM links WHERE token=? AND active=1", (token,)).fetchone()
        return dict(row) if row else None

    def deactivate_link(self, token: str) -> None:
        with self._lock:
            self._db.execute("UPDATE links SET active=0 WHERE token=?", (token,)); self._commit()

    def set_state(self, run_id: str, state: RunState) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?", (state.value, time.time(), run_id)); self._commit()

    def run(self, run_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_submission(self, run_id: str, submission: str) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET submission=?,updated_at=? WHERE run_id=?",
                            (submission, time.time(), run_id))
            self._commit()

    def add_usage(self, run_id: str, record_json: str, cost_usd: float) -> float:
        with self._lock:
            self._db.execute("INSERT INTO usage (run_id,record,ts) VALUES (?,?,?)", (run_id, record_json, time.time()))
            self._db.execute("UPDATE runs SET cost_usd=cost_usd+?, updated_at=? WHERE run_id=?", (cost_usd, time.time(), run_id))
            self._commit()
            return self.run(run_id)["cost_usd"]

    def total_spend(self) -> float:
        return self._db.execute("SELECT COALESCE(SUM(cost_usd),0) t FROM runs").fetchone()["t"]

    def add_artifact(self, run_id: str, kind: str, ref: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (run_id, kind, ref, time.time())); self._commit()

    def artifacts(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM artifacts WHERE run_id=?", (run_id,))]

    # UI task model.
    def create_task(self, title: str, brief: str = "", *, task_id: str | None = None) -> dict:
        with self._lock:
            task_id, now = task_id or secrets.token_hex(6), time.time()
            self._db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                            (task_id, title, brief, "not_started", None, None, 1, now, now))
            self._db.executemany(
                "INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (?,?,?,?, 'pending')",
                [(task_id, key, label, i) for i, (key, label) in enumerate(PHASES)],
            )
            self.audit("local user", "task.created", task_id, {"title": title})
            self._commit()
            return self.task(task_id)

    def list_tasks(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [self._task_record(r) for r in rows]

    def task(self, task_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._task_record(row) if row else None

    def _task_record(self, row) -> dict:
        item = dict(row)
        item["phases"] = [dict(r) for r in self._db.execute(
            "SELECT phase_key,label,position,state,detail,completed,total,gate_basis_changed,started_at,completed_at "
            "FROM phases WHERE task_id=? ORDER BY position", (item["task_id"],))]
        item["artifact_count"] = self._db.execute(
            "SELECT count(*) n FROM task_artifacts WHERE task_id=? AND stale=0", (item["task_id"],)).fetchone()["n"]
        return item

    def update_phase(self, task_id: str, phase_key: str, state: str, *, detail: str = "",
                     completed: int = 0, total: int | None = None) -> None:
        now = time.time()
        with self._lock:
            current = self._db.execute("SELECT state,started_at FROM phases WHERE task_id=? AND phase_key=?",
                                      (task_id, phase_key)).fetchone()
            if not current:
                raise KeyError(phase_key)
            started = current["started_at"] or (now if state == "running" else None)
            ended = now if state == "done" else None
            self._db.execute(
                "UPDATE phases SET state=?,detail=?,completed=?,total=?,started_at=?,completed_at=? "
                "WHERE task_id=? AND phase_key=?",
                (state, detail, completed, total, started, ended, task_id, phase_key),
            )
            task_state = "running" if state == "running" else ("failed" if state == "failed" else None)
            if state == "done" and phase_key == PHASES[-1][0]: task_state = "done"
            if task_state:
                self._db.execute("UPDATE tasks SET state=?,active_phase=?,updated_at=? WHERE task_id=?",
                                (task_state, phase_key, now, task_id))
            else:
                self._db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (now, task_id))
            self._commit()

    def set_model_route(self, task_id: str, model_route: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET model_route=?,updated_at=? WHERE task_id=?",
                            (model_route, time.time(), task_id)); self._commit()

    def add_event(self, task_id: str, kind: str, message: str, *, phase_key: str | None = None,
                  payload: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                                  (task_id, phase_key, kind, message, json.dumps(payload or {}), time.time()))
            self._db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (time.time(), task_id)); self._commit()
            return cur.lastrowid

    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        rows = self._db.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                               (task_id, after, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["payload"] = json.loads(item["payload"]); result.append(item)
        return result

    def audit(self, actor: str, action: str, task_id: str | None = None, detail: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO audit_log(actor,action,task_id,detail,ts) VALUES (?,?,?,?,?)",
                                  (actor, action, task_id, json.dumps(detail or {}), time.time()))
            self._commit()
            return cur.lastrowid

    def audit_entries(self, task_id: str | None = None, limit: int = 500) -> list[dict]:
        if task_id:
            rows = self._db.execute("SELECT * FROM audit_log WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
        else:
            rows = self._db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        result=[]
        for row in rows:
            item=dict(row); item["detail"]=json.loads(item["detail"]); result.append(item)
        return result

    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str,
                          mime_type: str = "application/json") -> None:
        task = self.task(task_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,mime_type,created_at) VALUES (?,?,?,?,?,?,?)",
                (task_id, task["generation"], phase_key, name, ref, mime_type, time.time()),
            ); self._commit()

    def task_artifacts(self, task_id: str, *, include_stale: bool = True) -> list[dict]:
        where = "task_id=?" if include_stale else "task_id=? AND stale=0"
        return [dict(r) for r in self._db.execute(
            f"SELECT * FROM task_artifacts WHERE {where} ORDER BY generation DESC,id", (task_id,))]

    def prepare_rerun(self, task_id: str, from_phase: str, policy: str = "archive", actor: str = "local user") -> int:
        """Policy B: stale now, retain by generation, require downstream re-approval."""
        if policy != "archive":
            raise ValueError("rerun policy is archive: downstream work is retained and marked stale")
        positions = dict((k, i) for i, (k, _) in enumerate(PHASES))
        if from_phase not in positions: raise KeyError(from_phase)
        with self._lock:
            downstream = [k for k, i in positions.items() if i >= positions[from_phase]]
            marks = ",".join("?" for _ in downstream)
            reason = f"generation basis changed: rerun from {from_phase}"
            self._db.execute(f"UPDATE task_artifacts SET stale=1,stale_reason=? WHERE task_id=? AND phase_key IN ({marks})",
                            (reason, task_id, *downstream))
            self._db.execute(f"UPDATE phases SET state='pending',detail='',completed=0,total=NULL,gate_basis_changed=1,started_at=NULL,completed_at=NULL "
                            f"WHERE task_id=? AND phase_key IN ({marks})", (task_id, *downstream))
            self._db.execute("UPDATE tasks SET generation=generation+1,state='not_started',active_phase=NULL,updated_at=? WHERE task_id=?",
                            (time.time(), task_id))
            generation = self.task(task_id)["generation"]
            self.audit(actor, "task.rerun_prepared", task_id, {"from_phase": from_phase, "generation": generation, "policy": "archive", "stale_reason": reason})
            self._commit()
            return generation


def create_store(database_url: str) -> Storage:
    """Select the persistence adapter in one place.

    Local paths and sqlite:/// URLs use SQLite. A future postgres:// adapter is
    registered here; no route, runner or job code changes with that deployment.
    """
    if database_url == ":memory:":
        return SQLiteStore(database_url)
    if database_url.startswith("sqlite:///"):
        return SQLiteStore(database_url.removeprefix("sqlite:///"))
    if "://" not in database_url:
        return SQLiteStore(database_url)  # backwards-compatible local path
    scheme = database_url.split(":", 1)[0]
    raise ValueError(
        f"unsupported database scheme {scheme!r}. "
        "Set DELIVERY_DATABASE_URL=sqlite:////absolute/path/to/delivery.db for local persistence. "
        "A cloud adapter must implement delivery_api.store.Storage and be registered in create_store."
    )
