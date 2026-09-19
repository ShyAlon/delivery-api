"""Durable task, phase, event and artifact state backed by SQLite."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from vmodel_contracts import RunState
from .observability import event as activity

SCHEMA = """

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL, email TEXT,
  label TEXT NOT NULL, token_quota INTEGER NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, password_hash TEXT, UNIQUE(provider,external_id)
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  token TEXT PRIMARY KEY, run_id TEXT NOT NULL, founder_label TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, state TEXT NOT NULL, submission TEXT, session_ref TEXT,
  cost_usd REAL NOT NULL DEFAULT 0, budget_usd REAL NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, record TEXT NOT NULL, ts REAL NOT NULL,
  user_id TEXT, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS artifacts (
  run_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, title TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, active_phase TEXT, model_route TEXT, generation INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, simulated INTEGER NOT NULL DEFAULT 0, source_dir TEXT, owner_id TEXT
);
CREATE TABLE IF NOT EXISTS phases (
  task_id TEXT NOT NULL, phase_key TEXT NOT NULL, label TEXT NOT NULL, position INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
  completed INTEGER NOT NULL DEFAULT 0, total INTEGER, gate_basis_changed INTEGER NOT NULL DEFAULT 0,
  started_at REAL, completed_at REAL, PRIMARY KEY(task_id, phase_key)
);
CREATE TABLE IF NOT EXISTS gates (
  task_id TEXT NOT NULL, gate_key TEXT NOT NULL, after_phase TEXT NOT NULL, label TEXT NOT NULL,
  position INTEGER NOT NULL, is_open INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'idle',
  options TEXT NOT NULL DEFAULT '[]', selected_option TEXT, edited_text TEXT, steer TEXT,
  PRIMARY KEY(task_id, gate_key)
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
    ("intent", "Start"),
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
    def rename_task(self, task_id: str, title: str, actor: str = "local user") -> dict: ...
    def delete_task(self, task_id: str, actor: str = "local user") -> None: ...
    def update_phase(self, task_id: str, phase_key: str, state: str, **progress) -> None: ...
    def set_model_route(self, task_id: str, model_route: str) -> None: ...
    def add_event(self, task_id: str, kind: str, message: str, **context) -> int: ...
    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]: ...
    def audit(self, actor: str, action: str, task_id: str | None = None, detail: dict | None = None) -> int: ...
    def audit_entries(self, task_id: str | None = None, limit: int = 500) -> list[dict]: ...
    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str, mime_type: str = "application/json") -> None: ...
    def task_artifacts(self, task_id: str, *, include_stale: bool = True) -> list[dict]: ...
    def rerun_prerequisite(self, task_id: str, from_phase: str) -> dict | None: ...
    def prepare_rerun(self, task_id: str, from_phase: str, policy: str = "archive", actor: str = "local user") -> int: ...
    def configure_simulation(self, task_id: str, source_dir: str, model_route: str) -> None: ...
    def set_gate_open(self, task_id: str, gate_key: str, is_open: bool, actor: str = "local user") -> dict: ...
    def wait_at_gate(self, task_id: str, after_phase: str) -> dict | None: ...
    def decide_gate(self, task_id: str, gate_key: str, action: str, **decision) -> dict: ...


class SQLiteStore:
    def __init__(self, path: str, connection=None):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = connection or sqlite3.connect(path, check_same_thread=False)
        if connection is None:self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.executescript(SCHEMA)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Small additive migrations keep local task history readable."""
        phase_cols = {r[1] for r in self._db.execute("PRAGMA table_info(phases)")}
        artifact_cols = {r[1] for r in self._db.execute("PRAGMA table_info(task_artifacts)")}
        task_cols = {r[1] for r in self._db.execute("PRAGMA table_info(tasks)")}
        run_cols = {r[1] for r in self._db.execute("PRAGMA table_info(runs)")}
        usage_cols = {r[1] for r in self._db.execute("PRAGMA table_info(usage)")}
        user_cols = {r[1] for r in self._db.execute("PRAGMA table_info(users)")}
        if "password_hash" not in user_cols:
            self._db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "gate_basis_changed" not in phase_cols:
            self._db.execute("ALTER TABLE phases ADD COLUMN gate_basis_changed INTEGER NOT NULL DEFAULT 0")
        if "stale_reason" not in artifact_cols:
            self._db.execute("ALTER TABLE task_artifacts ADD COLUMN stale_reason TEXT")
        if "simulated" not in task_cols:
            self._db.execute("ALTER TABLE tasks ADD COLUMN simulated INTEGER NOT NULL DEFAULT 0")
        if "source_dir" not in task_cols:
            self._db.execute("ALTER TABLE tasks ADD COLUMN source_dir TEXT")
        if "owner_id" not in task_cols:
            self._db.execute("ALTER TABLE tasks ADD COLUMN owner_id TEXT")
        if "owner_id" not in run_cols:
            self._db.execute("ALTER TABLE runs ADD COLUMN owner_id TEXT")
        # Older databases may contain duplicate labels. Preserve every task and
        # make collisions explicit before installing the uniqueness invariant.
        duplicates=self._db.execute("SELECT lower(title) k FROM tasks GROUP BY lower(title) HAVING count(*)>1").fetchall()
        for dup in duplicates:
            rows=self._db.execute("SELECT task_id,title FROM tasks WHERE lower(title)=? ORDER BY created_at,task_id",(dup["k"],)).fetchall()
            for i,row in enumerate(rows[1:],2):
                self._db.execute("UPDATE tasks SET title=? WHERE task_id=?",(f"{row['title']} ({i})",row["task_id"]))
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_title_nocase ON tasks(title COLLATE NOCASE)")
        for col, ddl in (("user_id", "TEXT"), ("input_tokens", "INTEGER NOT NULL DEFAULT 0"), ("output_tokens", "INTEGER NOT NULL DEFAULT 0")):
            if col not in usage_cols: self._db.execute(f"ALTER TABLE usage ADD COLUMN {col} {ddl}")
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
            title=title.strip()
            if not title: raise ValueError("task name cannot be empty")
            try:
                self._db.execute("INSERT INTO tasks(task_id,title,brief,state,active_phase,model_route,generation,created_at,updated_at,simulated,source_dir) VALUES (?,?,?,?,?,?,?,?,?,0,NULL)",
                                (task_id, title, brief, "not_started", None, None, 1, now, now))
            except sqlite3.IntegrityError as e:
                raise ValueError(f"a task named {title!r} already exists") from e
            self._db.executemany(
                "INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (?,?,?,?, 'pending')",
                [(task_id, key, label, i) for i, (key, label) in enumerate(PHASES)],
            )
            self._db.executemany(
                "INSERT INTO gates(task_id,gate_key,after_phase,label,position,is_open,state,options) VALUES (?,?,?,?,?,0,'idle',?)",
                [(task_id, f"gate-{key}", key, f"{label} gate", i,
                  json.dumps([f"Approve {label}", "Choose another option"]))
                 for i, (key, label) in enumerate(PHASES[:-1])],
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

    def rename_task(self, task_id: str, title: str, actor: str = "local user") -> dict:
        title=title.strip()
        if not title: raise ValueError("task name cannot be empty")
        with self._lock:
            if not self.task(task_id): raise KeyError(task_id)
            try:
                self._db.execute("UPDATE tasks SET title=?,updated_at=? WHERE task_id=?",(title,time.time(),task_id))
                self.audit(actor,"task.renamed",task_id,{"title":title});self._commit()
            except sqlite3.IntegrityError as e:
                raise ValueError(f"a task named {title!r} already exists") from e
            return self.task(task_id)

    def delete_task(self, task_id: str, actor: str = "local user") -> None:
        with self._lock:
            row=self._db.execute("SELECT title,state FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row: raise KeyError(task_id)
            if row["state"] in ("running","waiting_gate"):
                raise ValueError("a running or paused replay cannot be deleted")
            # Delete all UI-owned task data. Source artifacts are references and
            # remain untouched on disk. Keep a tombstone audit entry.
            for table in ("phases","gates","events","task_artifacts"):
                self._db.execute(f"DELETE FROM {table} WHERE task_id=?",(task_id,))
            self._db.execute("DELETE FROM tasks WHERE task_id=?",(task_id,))
            self.audit(actor,"task.deleted",task_id,{"title":row["title"],"source_artifacts_deleted":False});self._commit()

    def _task_record(self, row) -> dict:
        item = dict(row)
        item["phases"] = [dict(r) for r in self._db.execute(
            "SELECT phase_key,label,position,state,detail,completed,total,gate_basis_changed,started_at,completed_at "
            "FROM phases WHERE task_id=? ORDER BY position", (item["task_id"],))]
        item["gates"] = [self._gate_record(r) for r in self._db.execute(
            "SELECT * FROM gates WHERE task_id=? ORDER BY position", (item["task_id"],))]
        item["artifact_count"] = self._db.execute(
            "SELECT count(*) n FROM task_artifacts WHERE task_id=? AND stale=0", (item["task_id"],)).fetchone()["n"]
        return item

    def _gate_record(self, row) -> dict:
        item = dict(row); item["options"] = json.loads(item["options"]); return item

    def configure_simulation(self, task_id: str, source_dir: str, model_route: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET simulated=1,source_dir=?,model_route=?,updated_at=? WHERE task_id=?",
                             (source_dir, model_route, time.time(), task_id)); self._commit()

    def set_gate_open(self, task_id: str, gate_key: str, is_open: bool, actor: str = "local user") -> dict:
        with self._lock:
            self._db.execute("UPDATE gates SET is_open=? WHERE task_id=? AND gate_key=?", (int(is_open), task_id, gate_key))
            self.audit(actor, "gate.toggled", task_id, {"gate": gate_key, "open": is_open, "provenance": "interactive V gate"})
            self._commit()
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,gate_key)).fetchone()
            if not row: raise KeyError(gate_key)
            return self._gate_record(row)

    def wait_at_gate(self, task_id: str, after_phase: str) -> dict | None:
        with self._lock:
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND after_phase=?",(task_id,after_phase)).fetchone()
            if not row or row["is_open"]: return None
            self._db.execute("UPDATE gates SET state='waiting' WHERE task_id=? AND gate_key=?",(task_id,row["gate_key"]))
            self._db.execute("UPDATE tasks SET state='waiting_gate',active_phase=?,updated_at=? WHERE task_id=?",(after_phase,time.time(),task_id))
            self._commit(); return self._gate_record(self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,row["gate_key"])).fetchone())

    def decide_gate(self, task_id: str, gate_key: str, action: str, *, selected_option: str | None = None,
                    edited_text: str | None = None, steer: str | None = None, actor: str = "local user") -> dict:
        if action not in {"approve", "edit", "redo"}: raise ValueError("action must be approve, edit, or redo")
        with self._lock:
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,gate_key)).fetchone()
            if not row: raise KeyError(gate_key)
            if action == "approve" and not selected_option: raise ValueError("choose an option to approve")
            if action == "edit" and not edited_text: raise ValueError("edited option text is required")
            if action == "redo" and not steer: raise ValueError("a redo needs a reason so it is a steer, not a reroll")
            state = "redo" if action == "redo" else "decided"
            self._db.execute("UPDATE gates SET state=?,selected_option=?,edited_text=?,steer=? WHERE task_id=? AND gate_key=?",
                             (state,selected_option,edited_text,steer,task_id,gate_key))
            self.audit(actor, f"gate.{action}", task_id, {"gate":gate_key,"selected_option":selected_option,"edited_text":edited_text,"steer":steer,"provenance":"interactive V gate"})
            self._db.execute("UPDATE tasks SET state='running',updated_at=? WHERE task_id=?",(time.time(),task_id)); self._commit()
            return self._gate_record(self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,gate_key)).fetchone())

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

    def rerun_prerequisite(self, task_id: str, from_phase: str) -> dict | None:
        """Return the first unfulfilled upstream phase, or None when rerun is valid."""
        positions = {key: i for i, (key, _) in enumerate(PHASES)}
        if from_phase not in positions: raise KeyError(from_phase)
        phases = self._db.execute(
            "SELECT phase_key,label,state FROM phases WHERE task_id=? ORDER BY position", (task_id,)
        ).fetchall()
        if not phases: raise KeyError(task_id)
        for phase in phases[:positions[from_phase]]:
            if phase["state"] != "done": return dict(phase)
        return None

    def prepare_rerun(self, task_id: str, from_phase: str, policy: str = "archive", actor: str = "local user") -> int:
        """Policy B: stale now, retain by generation, require downstream re-approval."""
        if policy != "archive":
            raise ValueError("rerun policy is archive: downstream work is retained and marked stale")
        positions = dict((k, i) for i, (k, _) in enumerate(PHASES))
        if from_phase not in positions: raise KeyError(from_phase)
        missing = self.rerun_prerequisite(task_id, from_phase)
        if missing: raise ValueError(f"prerequisite not fulfilled: {missing['label']} ({missing['phase_key']})")
        with self._lock:
            downstream = [k for k, i in positions.items() if i >= positions[from_phase]]
            marks = ",".join("?" for _ in downstream)
            reason = f"generation basis changed: rerun from {from_phase}"
            self._db.execute(f"UPDATE task_artifacts SET stale=1,stale_reason=? WHERE task_id=? AND phase_key IN ({marks})",
                            (reason, task_id, *downstream))
            self._db.execute(f"UPDATE phases SET state='pending',detail='',completed=0,total=NULL,gate_basis_changed=1,started_at=NULL,completed_at=NULL "
                            f"WHERE task_id=? AND phase_key IN ({marks})", (task_id, *downstream))
            gate_phases = [k for k in downstream if k != PHASES[-1][0]]
            if gate_phases:
                gate_marks = ",".join("?" for _ in gate_phases)
                self._db.execute(f"UPDATE gates SET is_open=0,state='idle',selected_option=NULL,edited_text=NULL,steer=NULL "
                                 f"WHERE task_id=? AND after_phase IN ({gate_marks})", (task_id, *gate_phases))
            self._db.execute("UPDATE tasks SET generation=generation+1,state='not_started',active_phase=NULL,updated_at=? WHERE task_id=?",
                            (time.time(), task_id))
            generation = self.task(task_id)["generation"]
            self.audit(actor, "task.rerun_prepared", task_id, {"from_phase": from_phase, "generation": generation, "policy": "archive", "stale_reason": reason})
            self._commit()
            return generation


    # Authentication and per-user quota seam.
    def create_oauth_state(self, state_hash: str, provider: str, expires_at: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO oauth_states VALUES (?,?,?)", (state_hash, provider, expires_at)); self._commit()

    def consume_oauth_state(self, state_hash: str, provider: str, now: float) -> bool:
        with self._lock:
            row=self._db.execute("SELECT * FROM oauth_states WHERE state_hash=? AND provider=? AND expires_at>?",(state_hash,provider,now)).fetchone()
            self._db.execute("DELETE FROM oauth_states WHERE state_hash=?",(state_hash,)); self._commit(); return bool(row)

    def upsert_user(self, provider: str, external_id: str, email: str | None, label: str, token_quota: int, is_admin: bool) -> dict:
        now=time.time(); user_id=f"{provider}:{external_id}"
        with self._lock:
            self._db.execute("INSERT INTO users(user_id,provider,external_id,email,label,token_quota,is_admin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,external_id) DO UPDATE SET email=excluded.email,label=excluded.label,is_admin=excluded.is_admin,updated_at=excluded.updated_at",(user_id,provider,external_id,email,label,token_quota,int(is_admin),now,now)); self._commit()
            return dict(self._db.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone())

    def create_basic_user(self, username: str, password_hash: str, token_quota: int) -> dict:
        now=time.time();username=username.strip().lower();user_id=f"basic:{username}"
        with self._lock:
            try:self._db.execute("INSERT INTO users(user_id,provider,external_id,label,token_quota,is_admin,created_at,updated_at,password_hash) VALUES(?,?,?,?,?,0,?,?,?)",(user_id,"basic",username,username,token_quota,now,now,password_hash));self._commit()
            except sqlite3.IntegrityError as e:raise ValueError("That username is already in use.") from e
            return dict(self._db.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone())

    def basic_user(self, username: str) -> dict | None:
        row=self._db.execute("SELECT * FROM users WHERE provider='basic' AND external_id=?",(username.strip().lower(),)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def create_session(self, session_id: str, user_id: str, expires_at: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO sessions VALUES (?,?,?,?)",(self._session_key(session_id),user_id,expires_at,time.time())); self._commit()

    def session(self, session_id: str) -> dict | None:
        row=self._db.execute("SELECT s.*,u.label,u.email,u.is_admin FROM sessions s JOIN users u USING(user_id) WHERE session_id=?",(self._session_key(session_id),)).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str) -> None:
        with self._lock: self._db.execute("DELETE FROM sessions WHERE session_id=?",(self._session_key(session_id),)); self._commit()

    def user_quota(self, user_id: str) -> dict | None:
        row=self._db.execute("SELECT user_id,label,email,token_quota,is_admin FROM users WHERE user_id=?",(user_id,)).fetchone()
        if not row: return None
        out=dict(row); out["tokens_used"]=self._db.execute("SELECT COALESCE(SUM(input_tokens+output_tokens),0) n FROM usage WHERE user_id=?",(user_id,)).fetchone()["n"]; return out

    def set_user_quota(self, user_id: str, token_quota: int, actor: str) -> dict:
        with self._lock:
            if not self._db.execute("SELECT 1 FROM users WHERE user_id=?",(user_id,)).fetchone(): raise KeyError(user_id)
            self._db.execute("UPDATE users SET token_quota=?,updated_at=? WHERE user_id=?",(token_quota,time.time(),user_id)); self.audit(actor,"quota.configured",detail={"user_id":user_id,"token_quota":token_quota}); self._commit(); return self.user_quota(user_id)

    def quota_allows(self, user_id: str) -> tuple[bool, dict]:
        q=self.user_quota(user_id)
        if not q: return False,{"user_id":user_id,"token_quota":0,"tokens_used":0}
        return q["tokens_used"] < q["token_quota"],q

    def attribute_run(self, run_id: str, user_id: str) -> None:
        with self._lock: self._db.execute("UPDATE runs SET owner_id=? WHERE run_id=?",(user_id,run_id)); self._commit()

    def add_user_usage(self, run_id: str, user_id: str, record_json: str, cost_usd: float, input_tokens: int, output_tokens: int) -> float:
        with self._lock:
            self._db.execute("INSERT INTO usage(run_id,record,ts,user_id,input_tokens,output_tokens) VALUES(?,?,?,?,?,?)",(run_id,record_json,time.time(),user_id,input_tokens,output_tokens)); self._db.execute("UPDATE runs SET cost_usd=cost_usd+?,updated_at=? WHERE run_id=?",(cost_usd,time.time(),run_id)); self._commit(); return self.run(run_id)["cost_usd"]


class TursoReplicaStore(SQLiteStore):
    """Local libSQL replica; cloud sync is best-effort and never breaks requests."""
    def __init__(self,path:str,url:str,token:str,sync_interval:int=60,connection=None):
        self._replica_available=True
        if connection is None:
            from .libsql_store import connect_replica
            try:connection=connect_replica(Path(path),url,token,sync_interval)
            except Exception as e:
                # libsql performs an initial pull while connecting. On a first
                # offline start that can fail before returning a connection;
                # keep the product usable on the stable SQLite file and retry
                # replica mode on the next restart.
                connection=None;self._replica_available=False
                activity("database.replica.offline","Turso unavailable at startup; using the local database",error=f"{type(e).__name__}: {str(e)[:180]}")
        self._sync_url=url
        if connection is not None:
            from .libsql_store import normalize_connection
            connection=normalize_connection(connection)
        super().__init__(path,connection=connection)
        self._safe_sync("schema bootstrap")
    def _safe_sync(self,reason:str)->None:
        if not self._replica_available:
            activity("database.sync.failed","Turso replica sync deferred; local data remains available",reason=reason,error="replica connection unavailable; retry occurs on restart")
            return
        try:self._db.sync();activity("database.sync","Turso replica synchronized",reason=reason)
        except Exception as e:activity("database.sync.failed","Turso replica sync deferred; local data remains available",reason=reason,error=f"{type(e).__name__}: {str(e)[:180]}")
    def create_basic_user(self,*args,**kwargs):
        result=super().create_basic_user(*args,**kwargs);self._safe_sync("account create");return result
    def create_session(self,*args,**kwargs):
        result=super().create_session(*args,**kwargs);self._safe_sync("session create");return result
    def delete_session(self,*args,**kwargs):
        result=super().delete_session(*args,**kwargs);self._safe_sync("session revoke");return result

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
