"""Durable task, phase, event and artifact state backed by SQLite."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time

from . import event_bus
from pathlib import Path
from typing import Protocol, runtime_checkable

from vmodel_contracts import RunState
from .observability import event as activity

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_comments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, wait_id TEXT NOT NULL,
 revision TEXT NOT NULL, actor TEXT NOT NULL, comment TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_reviews (
 task_id TEXT NOT NULL, wait_id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(task_id,wait_id,revision)
);
CREATE TABLE IF NOT EXISTS decision_drafts (
 task_id TEXT NOT NULL, wait_id TEXT NOT NULL, revision TEXT NOT NULL, draft_id TEXT NOT NULL,
 payload TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(task_id,wait_id,revision,draft_id)
);


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
  state TEXT NOT NULL, active_phase TEXT, model_route TEXT, model_default TEXT, generation INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, simulated INTEGER NOT NULL DEFAULT 0, source_dir TEXT, owner_id TEXT,
  engine_session_id TEXT,
  interview_schema_id TEXT, interview_schema_version TEXT
);
CREATE TABLE IF NOT EXISTS phases (
  task_id TEXT NOT NULL, phase_key TEXT NOT NULL, label TEXT NOT NULL, position INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
  completed INTEGER NOT NULL DEFAULT 0, total INTEGER, gate_basis_changed INTEGER NOT NULL DEFAULT 0,
  started_at REAL, completed_at REAL, PRIMARY KEY(task_id, phase_key)
);
CREATE TABLE IF NOT EXISTS gates (
  task_id TEXT NOT NULL, gate_key TEXT NOT NULL, after_phase TEXT NOT NULL, label TEXT NOT NULL,
  position INTEGER NOT NULL, is_open INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'idle', revision INTEGER NOT NULL DEFAULT 0,
  options TEXT NOT NULL DEFAULT '[]', selected_option TEXT, edited_text TEXT, steer TEXT,
  PRIMARY KEY(task_id, gate_key)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, phase_key TEXT,
  kind TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
CREATE TABLE IF NOT EXISTS progress_ingest (
 task_id TEXT NOT NULL, source TEXT NOT NULL, session_id TEXT NOT NULL,
 cursor INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(task_id,source,session_id)
);
CREATE TABLE IF NOT EXISTS progress_seen (
 task_id TEXT NOT NULL, source TEXT NOT NULL, event_id TEXT NOT NULL,
 PRIMARY KEY(task_id,source,event_id)
);
CREATE TABLE IF NOT EXISTS operator_failure_diagnostics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL, source TEXT NOT NULL,
  http_status INTEGER NOT NULL, error_class TEXT NOT NULL, sqlstate TEXT, stage TEXT,
  role TEXT NOT NULL, sub_present TEXT NOT NULL, session_id_present TEXT NOT NULL,
  session_active_for_sub TEXT NOT NULL, message TEXT NOT NULL, block TEXT NOT NULL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_failure_diagnostics_task ON operator_failure_diagnostics(task_id, id);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
  task_id TEXT, detail TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id, id);
CREATE TABLE IF NOT EXISTS answer_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, session_id TEXT,
  input_ref TEXT NOT NULL, target TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  thumb INTEGER, comment TEXT, actor TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(task_id, input_ref, target, actor)
);
CREATE TABLE IF NOT EXISTS task_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, generation INTEGER NOT NULL,
  phase_key TEXT NOT NULL, name TEXT NOT NULL, ref TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'application/json',
  stale INTEGER NOT NULL DEFAULT 0, stale_reason TEXT, created_at REAL NOT NULL
);
"""

from .phases import PHASE_DEFINITIONS, PHASE_POSITIONS, followers, predecessors

# Compatibility projection for adapters and callers while the canonical source
# remains PhaseDefinition.
PHASES = tuple((phase.key, phase.label) for phase in PHASE_DEFINITIONS)


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
    def set_model_default(self, task_id: str, model: str) -> None: ...
    def set_engine_session(self, task_id: str, session_id: str) -> None: ...
    def engine_session(self, task_id: str) -> str | None: ...
    def bind_interview_schema(self, task_id: str, schema_id: str, schema_version: str) -> dict: ...
    def interview_schema_binding(self, task_id: str) -> dict | None: ...
    def add_event(self, task_id: str, kind: str, message: str, **context) -> int: ...
    def record_failure_diagnostics(self, task_id: str, source: str, http_status: int, block: dict) -> int: ...
    def failure_diagnostics(self, task_id: str, limit: int = 20) -> list[dict]: ...
    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]: ...
    def progress_cursor(self, task_id: str, source: str, session_id: str) -> int: ...
    def ingest_progress(self, signal, source: str) -> bool: ...
    def audit(self, actor: str, action: str, task_id: str | None = None, detail: dict | None = None) -> int: ...
    def audit_entries(self, task_id: str | None = None, limit: int = 500) -> list[dict]: ...
    def add_task_artifact(self, task_id: str, phase_key: str, name: str, ref: str, mime_type: str = "application/json") -> None: ...
    def list_feedback(self, task_id: str) -> list[dict]: ...
    def upsert_feedback(self, task_id: str, input_ref: str, target: str, content: str, thumb: int | None, comment: str | None, actor: str) -> dict: ...
    def update_feedback(self, task_id: str, feedback_id: int, actor: str, *, thumb: int | None = None, comment: str | None = None, admin: bool = False) -> dict: ...
    def delete_feedback(self, task_id: str, feedback_id: int, actor: str, *, admin: bool = False) -> None: ...
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

    def _add_column(self, ddl: str) -> None:
        """ALTER TABLE ... ADD COLUMN that tolerates the column already existing.

        The PRAGMA check above each call can read a stale local replica while the
        ALTER runs on the remote primary, which may already have the column.
        """
        with self._lock:
            try:
                self._db.execute(ddl)
            except Exception as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _migrate_schema(self) -> None:
        """Small additive migrations keep local task history readable."""
        with self._lock:
            gate_cols = {r[1] for r in self._db.execute("PRAGMA table_info(gates)")}
            if "revision" not in gate_cols:
                self._add_column("ALTER TABLE gates ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            phase_cols = {r[1] for r in self._db.execute("PRAGMA table_info(phases)")}
            artifact_cols = {r[1] for r in self._db.execute("PRAGMA table_info(task_artifacts)")}
            task_cols = {r[1] for r in self._db.execute("PRAGMA table_info(tasks)")}
            run_cols = {r[1] for r in self._db.execute("PRAGMA table_info(runs)")}
            usage_cols = {r[1] for r in self._db.execute("PRAGMA table_info(usage)")}
            user_cols = {r[1] for r in self._db.execute("PRAGMA table_info(users)")}
            if "password_hash" not in user_cols:
                self._add_column("ALTER TABLE users ADD COLUMN password_hash TEXT")
            if "gate_basis_changed" not in phase_cols:
                self._add_column("ALTER TABLE phases ADD COLUMN gate_basis_changed INTEGER NOT NULL DEFAULT 0")
            if "stale_reason" not in artifact_cols:
                self._add_column("ALTER TABLE task_artifacts ADD COLUMN stale_reason TEXT")
            if "simulated" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN simulated INTEGER NOT NULL DEFAULT 0")
            if "source_dir" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN source_dir TEXT")
            if "owner_id" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN owner_id TEXT")
            if "engine_session_id" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN engine_session_id TEXT")
            if "interview_schema_id" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN interview_schema_id TEXT")
            if "interview_schema_version" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN interview_schema_version TEXT")
            if "model_default" not in task_cols:
                self._add_column("ALTER TABLE tasks ADD COLUMN model_default TEXT")
            if "owner_id" not in run_cols:
                self._add_column("ALTER TABLE runs ADD COLUMN owner_id TEXT")
            # Every generated phase, including final verification, has a founder
            # approval boundary. Older databases omitted the final gate and could
            # strand a completed tests process with no ActiveWork owner.
            tasks=self._db.execute("SELECT task_id FROM tasks").fetchall()
            for task in tasks:
                existing={row[0] for row in self._db.execute("SELECT after_phase FROM gates WHERE task_id=?",(task["task_id"],))}
                for position,(key,label) in enumerate(PHASES):
                    if key not in existing:
                        self._db.execute("INSERT INTO gates(task_id,gate_key,after_phase,label,position,is_open,state,options) VALUES (?,?,?,?,?,0,'idle',?)",(task["task_id"],f"gate-{key}",key,f"{label} gate",position,json.dumps([f"Approve {label}","Choose another option"])))
            # A phase added after a task was created gets a pending row, so every
            # task lists the same phases. Finished tasks stay finished.
            for task in tasks:
                have={row[0] for row in self._db.execute("SELECT phase_key FROM phases WHERE task_id=?",(task["task_id"],))}
                for position,(key,label) in enumerate(PHASES):
                    if key not in have:
                        self._db.execute("INSERT INTO phases(task_id,phase_key,label,position,state) VALUES (?,?,?,?, 'pending')",(task["task_id"],key,label,position))
            # Older databases may contain duplicate labels. Preserve every task and
            # make collisions explicit before installing the uniqueness invariant.
            duplicates=self._db.execute("SELECT lower(title) k FROM tasks GROUP BY lower(title) HAVING count(*)>1").fetchall()
            for dup in duplicates:
                rows=self._db.execute("SELECT task_id,title FROM tasks WHERE lower(title)=? ORDER BY created_at,task_id",(dup["k"],)).fetchall()
                for i,row in enumerate(rows[1:],2):
                    self._db.execute("UPDATE tasks SET title=? WHERE task_id=?",(f"{row['title']} ({i})",row["task_id"]))
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_title_nocase ON tasks(title COLLATE NOCASE)")
            for col, ddl in (("user_id", "TEXT"), ("input_tokens", "INTEGER NOT NULL DEFAULT 0"), ("output_tokens", "INTEGER NOT NULL DEFAULT 0")):
                if col not in usage_cols: self._add_column(f"ALTER TABLE usage ADD COLUMN {col} {ddl}")
            self._db.commit()

    def _commit(self):
        with self._lock:
            self._db.commit()
            from . import task_read_cache
            task_read_cache.invalidate(self)

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
        with self._lock:
            row = self._db.execute("SELECT * FROM links WHERE token=? AND active=1", (token,)).fetchone()
            return dict(row) if row else None

    def deactivate_link(self, token: str) -> None:
        with self._lock:
            self._db.execute("UPDATE links SET active=0 WHERE token=?", (token,)); self._commit()

    def set_state(self, run_id: str, state: RunState) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?", (state.value, time.time(), run_id)); self._commit()

    def run(self, run_id: str) -> dict | None:
        with self._lock:
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
        with self._lock:
            return self._db.execute("SELECT COALESCE(SUM(cost_usd),0) t FROM runs").fetchone()["t"]

    def add_artifact(self, run_id: str, kind: str, ref: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (run_id, kind, ref, time.time())); self._commit()

    def artifacts(self, run_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM artifacts WHERE run_id=?", (run_id,))]

    # UI task model.
    def create_task(self, title: str, brief: str = "", *, task_id: str | None = None) -> dict:
        with self._lock:
            task_id, now = task_id or secrets.token_hex(6), time.time()
            title=title.strip()
            if not title: raise ValueError("task name cannot be empty")
            try:
                self._db.execute("INSERT INTO tasks(task_id,title,brief,state,active_phase,model_route,generation,created_at,updated_at,simulated,source_dir) VALUES (?,?,?,?,?,?,?,?,?,0,NULL)",
                                (task_id, title, brief, "not_started", None, "deepseek", 1, now, now))
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
                 for i, (key, label) in enumerate(PHASES)],
            )
            self.audit("local user", "task.created", task_id, {"title": title})
            self._commit()
            return self.task(task_id)

    # The whole task projection in one statement: every extra statement is a
    # full round trip on a remote database, and the UI polls these reads.
    _TASK_PROJECTION = """
SELECT t.*,
  (SELECT json_group_array(json(r.j)) FROM (
     SELECT json_object('phase_key',p.phase_key,'label',p.label,'position',p.position,'state',p.state,
                        'detail',p.detail,'completed',p.completed,'total',p.total,
                        'gate_basis_changed',p.gate_basis_changed,'started_at',p.started_at,'completed_at',p.completed_at) AS j
     FROM phases p WHERE p.task_id=t.task_id ORDER BY p.position) r) AS phases_json,
  (SELECT json_group_array(json(r.j)) FROM (
     SELECT json_object('task_id',g.task_id,'gate_key',g.gate_key,'after_phase',g.after_phase,'label',g.label,
                        'position',g.position,'is_open',g.is_open,'state',g.state,'revision',g.revision,
                        'options',g.options,'selected_option',g.selected_option,'edited_text',g.edited_text,'steer',g.steer) AS j
     FROM gates g WHERE g.task_id=t.task_id ORDER BY g.position) r) AS gates_json,
  (SELECT count(*) FROM task_artifacts a WHERE a.task_id=t.task_id AND a.stale=0) AS artifact_count
FROM tasks t"""

    # Newest N events ride the same statement as the task record: the hot task
    # read costs one round trip, not two. Payload stays text here and is parsed
    # per event in Python, exactly as events() does.
    _EVENTS_SUBQUERY = """
  (SELECT coalesce(nullif(json_group_array(json(r.j)),''),'[]') FROM (
     SELECT json_object('id',e.id,'task_id',e.task_id,'phase_key',e.phase_key,'kind',e.kind,
                        'message',e.message,'payload',e.payload,'ts',e.ts) AS j
     FROM (SELECT * FROM events WHERE task_id=t.task_id ORDER BY id DESC LIMIT ?) e) r) AS events_json"""
    _TASK_WHERE = " WHERE t.task_id=?"
    _ARTIFACTS_SUBQUERY = """
  (SELECT coalesce(json_group_array(json(r.j)), '[]') FROM (
    SELECT json_object('id',a.id,'task_id',a.task_id,'generation',a.generation,
      'phase_key',a.phase_key,'name',a.name,'ref',a.ref,'mime_type',a.mime_type,
      'stale',a.stale,'stale_reason',a.stale_reason,'created_at',a.created_at) AS j
    FROM task_artifacts a WHERE a.task_id=t.task_id ORDER BY generation DESC,id) r) AS artifacts_json"""

    def list_tasks(self) -> list[dict]:
        # A single connection is shared by API worker threads and background jobs.
        with self._lock:
            rows = self._db.execute(self._TASK_PROJECTION + " ORDER BY t.updated_at DESC").fetchall()
            return [self._task_record(r) for r in rows]

    def task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(self._TASK_PROJECTION + " WHERE t.task_id=?", (task_id,)).fetchone()
            return self._task_record(row) if row else None

    def _snapshot_artifacts_sql(self):
        return self._ARTIFACTS_SUBQUERY

    def task_snapshot(self, task_id: str, event_limit: int = 500) -> dict | None:
        """Task record + newest events in ONE statement - the whole cold cost of
        a task page read. Events come back newest-500 ascending, payload parsed,
        the same shape events() returns."""
        artifact_sql=self._snapshot_artifacts_sql()
        sql = self._TASK_PROJECTION.replace("\nFROM tasks t", ",\n" + self._EVENTS_SUBQUERY + (",\n" + artifact_sql if artifact_sql else "") + "\nFROM tasks t") + self._TASK_WHERE
        with self._lock:
            row = self._db.execute(sql, (event_limit, task_id)).fetchone()
            if row is None:
                return None
            item = dict(row)
            events_json = item.pop("events_json", "[]")
            artifacts = json.loads(item.pop("artifacts_json", "[]") or "[]")
            manifest = json.loads(item.pop("manifest_json", None) or "null")
            if manifest is not None and self._revision_hash(manifest['rows']) != manifest['revision_hash']:
                raise ValueError('readiness marker does not match the published manifest revision')
            task = self._task_record(item)
            events = [self._event_record(e) for e in reversed(json.loads(events_json or "[]"))]
            return {"task": task, "events": events, "artifacts": artifacts, "manifest": manifest}

    def _event_record(self, item: dict) -> dict:
        item = dict(item)
        item["payload"] = json.loads(item["payload"]) if isinstance(item.get("payload"), str) else item.get("payload", {})
        return item

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
                raise ValueError("a running or paused task cannot be deleted")
            # Delete all UI-owned task data. Source artifacts are references and
            # remain untouched on disk. Keep a tombstone audit entry.
            for table in ("phases","gates","events","progress_ingest","progress_seen","task_artifacts"):
                self._db.execute(f"DELETE FROM {table} WHERE task_id=?",(task_id,))
            self._db.execute("DELETE FROM tasks WHERE task_id=?",(task_id,))
            self.audit(actor,"task.deleted",task_id,{"title":row["title"],"source_artifacts_deleted":False});self._commit()

    def _task_record(self, row) -> dict:
        # The row already carries phases/gates as JSON and the artifact count,
        # fetched by _TASK_PROJECTION in the same statement as the task row.
        item = dict(row)
        item["phases"] = json.loads(item.pop("phases_json"))
        item["gates"] = [self._gate_record(g) for g in json.loads(item.pop("gates_json"))]
        item["artifact_count"] = int(item["artifact_count"])
        return item

    def _gate_record(self, row) -> dict:
        item = dict(row); item["options"] = json.loads(item["options"]); return item

    def configure_simulation(self, task_id: str, source_dir: str, model_route: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET simulated=1,source_dir=?,model_route=?,updated_at=? WHERE task_id=?",
                             (source_dir, model_route, time.time(), task_id)); self._commit()

    def set_gate_open(self, task_id: str, gate_key: str, is_open: bool, actor: str = "local user") -> dict:
        with self._lock:
            self._db.execute("UPDATE gates SET is_open=?,revision=revision+1 WHERE task_id=? AND gate_key=?", (int(is_open), task_id, gate_key))
            self.audit(actor, "gate.toggled", task_id, {"gate": gate_key, "open": is_open, "provenance": "interactive V gate"})
            self._commit()
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,gate_key)).fetchone()
            if not row: raise KeyError(gate_key)
            return self._gate_record(row)

    def wait_at_gate(self, task_id: str, after_phase: str) -> dict | None:
        with self._lock:
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND after_phase=?",(task_id,after_phase)).fetchone()
            if not row: return None
            now=time.time()
            if row["is_open"]:
                self._db.execute("UPDATE gates SET state='bypassed',revision=revision+1 WHERE task_id=? AND gate_key=?",(task_id,row["gate_key"]))
                self._db.execute("UPDATE phases SET state='approved',detail='Approved by explicit open-gate policy',completed_at=? WHERE task_id=? AND phase_key=?",(now,task_id,after_phase))
                self.audit("policy", "gate.bypassed", task_id, {"gate":row["gate_key"],"after_phase":after_phase,"approved_at":now,"provenance":"explicit open-gate policy"})
                self._commit();return None
            self._db.execute("UPDATE phases SET state='awaiting_approval',detail='Generated; waiting for founder approval' WHERE task_id=? AND phase_key=?",(task_id,after_phase))
            self._db.execute("UPDATE gates SET state='waiting',revision=revision+1 WHERE task_id=? AND gate_key=?",(task_id,row["gate_key"]))
            self._db.execute("UPDATE tasks SET state='waiting_gate',active_phase=?,updated_at=? WHERE task_id=?",(after_phase,now,task_id))
            self._commit(); return self._gate_record(self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,row["gate_key"])).fetchone())

    def decide_gate(self, task_id: str, gate_key: str, action: str, *, selected_option: str | None = None,
                    edited_text: str | None = None, steer: str | None = None, actor: str = "local user",
                    expected_revision: int | None = None) -> dict:
        if action not in {"approve", "edit", "redo"}: raise ValueError("action must be approve, edit, or redo")
        with self._lock:
            row=self._db.execute("SELECT * FROM gates WHERE task_id=? AND gate_key=?",(task_id,gate_key)).fetchone()
            if not row: raise KeyError(gate_key)
            if expected_revision is not None and row["revision"]!=expected_revision:
                raise ValueError("gate changed; refresh before deciding")
            if row["state"]!="waiting":raise ValueError("gate is not waiting for a decision")
            if action == "approve" and not selected_option: raise ValueError("choose an option to approve")
            if action == "edit" and not edited_text: raise ValueError("edited option text is required")
            if action == "redo" and not steer: raise ValueError("a redo needs a reason so it is a steer, not a reroll")
            state = "redo" if action == "redo" else "decided";now=time.time();phase=row["after_phase"]
            self._db.execute("UPDATE gates SET state=?,selected_option=?,edited_text=?,steer=?,revision=revision+1 WHERE task_id=? AND gate_key=?",
                             (state,selected_option,edited_text,steer,task_id,gate_key))
            if action == "redo":
                self._db.execute("UPDATE phases SET state='running',detail=?,completed=0,total=NULL,started_at=?,completed_at=NULL WHERE task_id=? AND phase_key=?",(f"Redo requested: {steer}",now,task_id,phase))
                downstream=[definition.key for definition in followers(phase)]
                if downstream:
                    marks=','.join('?' for _ in downstream);self._db.execute(f"UPDATE phases SET state='pending',detail='',completed=0,total=NULL,started_at=NULL,completed_at=NULL WHERE task_id=? AND phase_key IN ({marks})",(task_id,*downstream))
            else:
                detail="Founder approved edited artifact" if action=="edit" else "Founder approved"
                self._db.execute("UPDATE phases SET state='approved',detail=?,completed_at=? WHERE task_id=? AND phase_key=?",(detail,now,task_id,phase))
            self.audit(actor, f"gate.{action}", task_id, {"gate":gate_key,"phase":phase,"selected_option":selected_option,"edited_text":edited_text,"steer":steer,"approved_at":now if action != "redo" else None,"provenance":"interactive V gate"})
            task_state="done" if action!="redo" and phase==PHASES[-1][0] else "running"
            self._db.execute("UPDATE tasks SET state=?,active_phase=?,updated_at=? WHERE task_id=?",(task_state,phase,now,task_id)); self._commit()
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
            ended = now if state in ("done","approved","awaiting_approval") else None
            if state == "running":
                # A task has one active execution locus. Earlier generated work
                # may wait for approval, but must not remain pulsing blue.
                earlier=[phase.key for phase in predecessors(phase_key)]
                later=[phase.key for phase in followers(phase_key)]
                if earlier:
                    marks=','.join('?' for _ in earlier)
                    self._db.execute(
                        f"UPDATE phases SET state='awaiting_approval',detail=CASE WHEN detail='' THEN 'Generated; waiting for founder approval' ELSE detail END,completed_at=? "
                        f"WHERE task_id=? AND phase_key IN ({marks}) AND state='running'",(now,task_id,*earlier))
                if later:
                    marks=','.join('?' for _ in later)
                    self._db.execute(
                        f"UPDATE phases SET state='pending',detail='Blocked on upstream approval',completed=0,total=NULL,started_at=NULL,completed_at=NULL "
                        f"WHERE task_id=? AND phase_key IN ({marks}) AND state IN ('running','awaiting_approval')",(task_id,*later))
            self._db.execute(
                "UPDATE phases SET state=?,detail=?,completed=?,total=?,started_at=?,completed_at=? "
                "WHERE task_id=? AND phase_key=?",
                (state, detail, completed, total, started, ended, task_id, phase_key),
            )
            task_state = "running" if state == "running" else ("failed" if state == "failed" else None)
            if state in ("done","approved") and phase_key == PHASES[-1][0]: task_state = "done"
            if task_state:
                self._db.execute("UPDATE tasks SET state=?,active_phase=?,updated_at=? WHERE task_id=?",
                                (task_state, phase_key, now, task_id))
            else:
                self._db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (now, task_id))
            self._commit()

    def reconcile_completed_interview(self, task_id: str, session_id: str, sequence: int) -> bool:
        """Commit the missing HTTP interview boundary once; preserve downstream work."""
        with self._lock:
            row=self._db.execute("SELECT engine_session_id,state,active_phase FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row["engine_session_id"] != session_id:return False
            phases=self._db.execute("SELECT phase_key,state FROM phases WHERE task_id=? AND phase_key IN ('intent','conops')",(task_id,)).fetchall()
            states={phase["phase_key"]:phase["state"] for phase in phases}
            if states.keys() != {"intent","conops"}:return False
            if row["active_phase"] not in ("intent","conops") or row["state"] not in ("running","failed"):return False
            if states["conops"] in ("approved","done"):return False
            if any(value not in ("pending","running","failed","awaiting_approval") for value in states.values()):return False
            now=time.time()
            for phase in ("intent","conops"):
                self._db.execute("UPDATE phases SET state='approved',detail='Founder approved in the completed interview',started_at=COALESCE(started_at,?),completed_at=? WHERE task_id=? AND phase_key=?",
                                 (now,now,task_id,phase))
            self._db.execute("UPDATE tasks SET state='running',active_phase='conops',updated_at=? WHERE task_id=?",(now,task_id))
            self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                             (task_id,"conops","progress","Completed HTTP interview reconciled with V-process",json.dumps({"session_id":session_id,"sequence":sequence,"source":"terminal_engine_snapshot","handoff":"needs"}),now))
            self.audit("engine", "interview.reconciled", task_id, {"session_id":session_id,"sequence":sequence,"next_phase":"needs"})
            self._commit();event_bus.notify(task_id)
            return True

    def claim_failed_needs_retry(self, task_id: str, actor: str) -> bool:
        """Atomically claim only a failed Needs run with approved interview inputs."""
        with self._lock:
            row=self._db.execute("SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row["state"]!="failed" or row["active_phase"]!="needs" or not row["engine_session_id"]:return False
            states={p["phase_key"]:p["state"] for p in self._db.execute("SELECT phase_key,state FROM phases WHERE task_id=?",(task_id,))}
            if any(states.get(phase) not in ("approved","done") for phase in ("intent","conops")) or states.get("needs")!="failed":return False
            if any(states.get(phase) not in ("pending","failed") for phase in ("srs","architecture","specs","tests","validation")):return False
            now=time.time()
            self._db.execute("UPDATE phases SET state='running',detail='Retrying failed Stakeholder needs',completed=0,total=NULL,started_at=?,completed_at=NULL WHERE task_id=? AND phase_key='needs'",(now,task_id))
            self._db.execute("UPDATE tasks SET state='running',active_phase='needs',updated_at=? WHERE task_id=?",(now,task_id))
            self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                             (task_id,"needs","retry","Founder confirmed retry of failed Stakeholder needs",json.dumps({"actor":actor,"session_id":row["engine_session_id"]}),now))
            self.audit(actor,"phase.retry",task_id,{"phase":"needs","session_id":row["engine_session_id"]})
            self._commit();event_bus.notify(task_id);return True

    def claim_next_phase(self, task_id: str, phase: str, actor: str) -> bool:
        """Atomically move a task whose interview is approved into its first generator phase.

        A second click finds the phase already running and gets False, so one click starts one job."""
        from .phases import PHASE_KEYS, PHASE_BY_KEY
        if phase!="needs" or phase not in PHASE_BY_KEY:return False
        with self._lock:
            row=self._db.execute("SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row["state"]!="running" or row["active_phase"]!="conops" or not row["engine_session_id"]:return False
            states={p["phase_key"]:p["state"] for p in self._db.execute("SELECT phase_key,state FROM phases WHERE task_id=?",(task_id,))}
            if any(states.get(key) not in ("approved","done") for key in ("intent","conops")) or states.get(phase)!="pending":return False
            if any(states.get(key)!="pending" for key in PHASE_KEYS[PHASE_KEYS.index(phase)+1:]):return False
            now=time.time()
            self._db.execute("UPDATE phases SET state='running',detail=?,completed=0,total=NULL,started_at=?,completed_at=NULL WHERE task_id=? AND phase_key=?",
                             (f"Starting {PHASE_BY_KEY[phase].label}",now,task_id,phase))
            self._db.execute("UPDATE tasks SET state='running',active_phase=?,updated_at=? WHERE task_id=?",(phase,now,task_id))
            self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                             (task_id,phase,"progress",f"You started {PHASE_BY_KEY[phase].label}",json.dumps({"actor":actor,"session_id":row["engine_session_id"],"started":True}),now))
            self.audit(actor,"phase.start",task_id,{"phase":phase,"session_id":row["engine_session_id"]})
            self._commit();event_bus.notify(task_id);return True

    def claim_failed_phase_retry(self, task_id: str, phase: str, actor: str) -> bool:
        """Atomically claim a failed generator phase whose upstream phases are approved.

        Later phases may only be pending or failed, so a retry never overwrites finished work."""
        from .phases import PHASE_KEYS, PHASE_BY_KEY
        if phase not in PHASE_BY_KEY or PHASE_BY_KEY[phase].executor!="generator":return False
        with self._lock:
            row=self._db.execute("SELECT state,active_phase,engine_session_id FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row["state"]!="failed" or row["active_phase"]!=phase or not row["engine_session_id"]:return False
            states={p["phase_key"]:p["state"] for p in self._db.execute("SELECT phase_key,state FROM phases WHERE task_id=?",(task_id,))}
            index=PHASE_KEYS.index(phase)
            if states.get(phase)!="failed":return False
            if any(states.get(key) not in ("approved","done") for key in PHASE_KEYS[:index]):return False
            if any(states.get(key) not in ("pending","failed") for key in PHASE_KEYS[index+1:]):return False
            now=time.time()
            self._db.execute("UPDATE phases SET state='running',detail=?,completed=0,total=NULL,started_at=?,completed_at=NULL WHERE task_id=? AND phase_key=?",
                             (f"Retrying failed {PHASE_BY_KEY[phase].label}",now,task_id,phase))
            self._db.execute("UPDATE tasks SET state='running',active_phase=?,updated_at=? WHERE task_id=?",(phase,now,task_id))
            self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                             (task_id,phase,"retry",f"Founder confirmed retry of failed {PHASE_BY_KEY[phase].label}",json.dumps({"actor":actor,"session_id":row["engine_session_id"]}),now))
            self.audit(actor,"phase.retry",task_id,{"phase":phase,"session_id":row["engine_session_id"]})
            self._commit();event_bus.notify(task_id);return True

    def set_model_route(self, task_id: str, model_route: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET model_route=?,updated_at=? WHERE task_id=?",
                            (model_route, time.time(), task_id)); self._commit()

    def set_model_default(self, task_id: str, model: str) -> None:
        """Persist the exact model selected by the founder for subsequent phases."""
        with self._lock:
            self._db.execute("UPDATE tasks SET model_default=?,updated_at=? WHERE task_id=?",
                             (model,time.time(),task_id)); self._commit()

    def set_engine_session(self, task_id: str, session_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET engine_session_id=?,updated_at=? WHERE task_id=?",(session_id,time.time(),task_id))
            self._commit()

    def begin_interview_start(self, task_id: str, session_id: str, route: str) -> bool:
        """Reserve one Start durably before the engine call; duplicate clicks cannot bill twice."""
        with self._lock:
            now=time.time()
            result=self._db.execute("UPDATE tasks SET state='running',active_phase='intent',engine_session_id=?,model_route=?,updated_at=? WHERE task_id=? AND state='not_started' AND engine_session_id IS NULL",
                                    (session_id,route,now,task_id))
            if result.rowcount!=1:
                self._db.rollback()
                return False
            self._db.execute("UPDATE phases SET state='running',detail='Starting interview',started_at=? WHERE task_id=? AND phase_key='intent'",(now,task_id))
            self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                             (task_id,'intent','command','Interview starting',json.dumps({'session_id':session_id,'route':route}),now))
            self._commit();event_bus.notify(task_id)
            return True

    def engine_session(self, task_id: str) -> str | None:
        with self._lock:
            row=self._db.execute("SELECT engine_session_id FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            return row["engine_session_id"] if row else None

    def bind_interview_schema(self,task_id:str,schema_id:str,schema_version:str)->dict:
        with self._lock:
            row=self._db.execute("SELECT interview_schema_id,interview_schema_version FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if row is None:raise KeyError(task_id)
            existing=(row["interview_schema_id"],row["interview_schema_version"])
            requested=(schema_id,schema_version)
            if existing!=(None,None) and existing!=requested:raise ValueError("interview schema binding is immutable for a task generation")
            self._db.execute("UPDATE tasks SET interview_schema_id=?,interview_schema_version=?,updated_at=? WHERE task_id=?",(schema_id,schema_version,time.time(),task_id));self._commit()
            return {"task_id":task_id,"schema_id":schema_id,"schema_version":schema_version}

    def interview_schema_binding(self,task_id:str)->dict|None:
        with self._lock:
            row=self._db.execute("SELECT interview_schema_id,interview_schema_version FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if row is None:raise KeyError(task_id)
            if row["interview_schema_id"] is None:return None
            return {"task_id":task_id,"schema_id":row["interview_schema_id"],"schema_version":row["interview_schema_version"]}

    def add_event(self, task_id: str, kind: str, message: str, *, phase_key: str | None = None,
                  payload: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                                  (task_id, phase_key, kind, message, json.dumps(payload or {}), time.time()))
            self._db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (time.time(), task_id)); self._commit();event_bus.notify(task_id)
            return cur.lastrowid

    @staticmethod
    def _validated_diagnostics_block(block: dict) -> dict:
        """The operator block is presence-only by contract: booleans or the
        honest UNKNOWN, never claim values, bounded sanitized message."""
        if not isinstance(block, dict):
            raise ValueError('operator diagnostics block required')
        out = dict(block)
        for key in ('sub_present', 'session_id_present', 'session_active_for_sub'):
            if out.get(key) not in (True, False, 'UNKNOWN'):
                raise ValueError(f'operator diagnostics {key} must be a presence boolean or UNKNOWN')
        out['error_class'] = str(out.get('error_class') or 'UNKNOWN')[:120]
        out['role'] = str(out.get('role') or 'UNKNOWN')[:120]
        out['message'] = str(out.get('message') or '')[:300]
        if out.get('sqlstate') is not None:
            sqlstate = str(out['sqlstate'])
            if len(sqlstate) != 5:
                raise ValueError('operator diagnostics sqlstate is the five-character class code')
            out['sqlstate'] = sqlstate
        if out.get('stage') is not None:
            out['stage'] = str(out['stage'])[:80]
        return out

    def record_failure_diagnostics(self, task_id: str, source: str, http_status: int, block: dict) -> int:
        """Operator-only failure evidence. Deliberately NOT an event and NOT an
        audit entry: no customer-visible surface reads this table."""
        block = self._validated_diagnostics_block(block)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO operator_failure_diagnostics(owner_id,task_id,source,http_status,error_class,sqlstate,stage,role,"
                "sub_present,session_id_present,session_active_for_sub,message,block,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('', task_id, source, http_status, block['error_class'], block.get('sqlstate'), block.get('stage'),
                 block['role'], str(block['sub_present']), str(block['session_id_present']),
                 str(block['session_active_for_sub']), block['message'], json.dumps(block), time.time()))
            self._commit()
            return cur.lastrowid

    def failure_diagnostics(self, task_id: str, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('bounded diagnostics page required')
        with self._lock:
            rows = self._db.execute(
                "SELECT id,task_id,source,http_status,error_class,sqlstate,stage,role,sub_present,session_id_present,"
                "session_active_for_sub,message,block,ts FROM operator_failure_diagnostics WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task_id, limit)).fetchall()
            out = []
            for row in rows:
                row = dict(row)
                try:
                    row['block'] = json.loads(row['block'])
                except ValueError:
                    pass
                out.append(row)
            return out

    def add_review_comment(self,task_id:str,wait_id:str,revision:str,actor:str,comment:str)->int:
        with self._lock:
            cursor=self._db.execute("INSERT INTO review_comments(task_id,wait_id,revision,actor,comment,created_at) VALUES (?,?,?,?,?,?)",
                (task_id,wait_id,revision,actor,comment,time.time()));self._commit();return cursor.lastrowid

    def review_comments(self,task_id:str,wait_id:str,revision:str)->list[dict]:
        with self._lock:
            rows=self._db.execute("SELECT id,actor,comment,created_at FROM review_comments WHERE task_id=? AND wait_id=? AND revision=? ORDER BY id",
                (task_id,wait_id,revision)).fetchall()
            return [dict(row) for row in rows]

    def retain_decision_review(self, task_id:str, wait_id:str, revision:str, payload:dict)->None:
        with self._lock:
            # A decision can wait through hundreds of task reads; the evidence pin
            # must cost one read once pinned, not a write per read.
            retained=self._db.execute('SELECT payload FROM decision_reviews WHERE task_id=? AND wait_id=? AND revision=?',
                (task_id,wait_id,revision)).fetchone()
            if retained is not None:
                if json.loads(retained[0])!=json.loads(json.dumps(payload)):
                    raise ValueError('conflicting retained decision review revision')
                return
            self._db.execute("INSERT OR IGNORE INTO decision_reviews VALUES (?,?,?,?,?)",
                (task_id,wait_id,revision,json.dumps(payload),time.time()))
            retained=self._db.execute('SELECT payload FROM decision_reviews WHERE task_id=? AND wait_id=? AND revision=?',
                (task_id,wait_id,revision)).fetchone()
            if retained is None or json.loads(retained[0])!=json.loads(json.dumps(payload)):
                raise ValueError('conflicting retained decision review revision')
            self._commit()

    def decision_review(self,task_id:str,wait_id:str,revision:str|None=None)->dict|None:
        with self._lock:
            if revision:
                row=self._db.execute("SELECT payload FROM decision_reviews WHERE task_id=? AND wait_id=? AND revision=?",
                    (task_id,wait_id,revision)).fetchone()
            else:
                row=self._db.execute("SELECT payload FROM decision_reviews WHERE task_id=? AND wait_id=? ORDER BY created_at DESC LIMIT 1",
                    (task_id,wait_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save_decision_draft(self,task_id:str,wait_id:str,revision:str,payload:dict)->str:
        draft_id=secrets.token_urlsafe(18)
        with self._lock:
            self._db.execute("INSERT INTO decision_drafts VALUES (?,?,?,?,?,?)",
                (task_id,wait_id,revision,draft_id,json.dumps(payload),time.time()));self._commit()
        return draft_id

    def decision_draft(self,task_id:str,wait_id:str,revision:str,draft_id:str)->dict|None:
        with self._lock:
            row=self._db.execute("SELECT payload FROM decision_drafts WHERE task_id=? AND wait_id=? AND revision=? AND draft_id=?",
                (task_id,wait_id,revision,draft_id)).fetchone()
            return json.loads(row[0]) if row else None

    def progress_cursor(self, task_id: str, source: str, session_id: str) -> int:
        with self._lock:
            row=self._db.execute("SELECT cursor FROM progress_ingest WHERE task_id=? AND source=? AND session_id=?",
                                 (task_id,source,session_id)).fetchone()
            return int(row[0]) if row else 0

    def ingest_progress(self, signal, source: str) -> bool:
        """Deduplicate and commit task event + source cursor as one transaction."""
        from vmodel_contracts.progress_signal import ProgressSignal
        signal=ProgressSignal.model_validate(signal)
        with self._lock:
            row=self._db.execute("SELECT cursor FROM progress_ingest WHERE task_id=? AND source=? AND session_id=?",
                                 (signal.task_id,source,signal.session_id)).fetchone()
            cursor=int(row[0]) if row else 0
            # Engine cursor is monotonic across the session; a generator
            # subprocess may restart at 1, so its event_id is the dedupe key.
            if source=="interview" and signal.cursor<=cursor:return False
            if source=="interview" and signal.cursor!=cursor+1:
                raise ValueError("interview progress cursor gap")
            if self._db.execute("SELECT 1 FROM progress_seen WHERE task_id=? AND source=? AND event_id=?",
                                (signal.task_id,source,signal.event_id)).fetchone():return False
            message=signal.detail or signal.label
            payload={**signal.model_dump(mode="json"),"source":source}
            try:
                self._db.execute("INSERT INTO progress_seen VALUES (?,?,?)",(signal.task_id,source,signal.event_id))
                self._db.execute("INSERT INTO events(task_id,phase_key,kind,message,payload,ts) VALUES (?,?,?,?,?,?)",
                                 (signal.task_id,signal.phase_key,"progress",message,json.dumps(payload),time.time()))
                self._db.execute("INSERT INTO progress_ingest(task_id,source,session_id,cursor) VALUES (?,?,?,?) "
                                 "ON CONFLICT(task_id,source,session_id) DO UPDATE SET cursor=max(cursor,excluded.cursor)",
                                 (signal.task_id,source,signal.session_id,signal.cursor))
                self._db.execute("UPDATE phases SET detail=?,completed=COALESCE(?,completed),total=COALESCE(?,total) "
                                 "WHERE task_id=? AND phase_key=? AND state='running'",
                                 (message,signal.completed,signal.total,signal.task_id,signal.phase_key))
                self._db.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",(time.time(),signal.task_id))
                self._commit();event_bus.notify(signal.task_id)
            except Exception:
                self._db.rollback()
                raise
            return True

    def events(self, task_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        # Snapshot reads need the newest durable facts. The old ascending LIMIT
        # silently hid requirements on long-running tasks once they crossed 500
        # events. Incremental stream reads keep ascending order from `after`.
        with self._lock:
            if after:
                rows = self._db.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                                       (task_id, after, limit)).fetchall()
            else:
                rows = list(reversed(self._db.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?",(task_id,limit)
                ).fetchall()))
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
        with self._lock:
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

    def list_feedback(self, task_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM answer_feedback WHERE task_id=? ORDER BY id", (task_id,))]

    def upsert_feedback(self, task_id: str, input_ref: str, target: str, content: str,
                        thumb: int | None, comment: str | None, actor: str) -> dict:
        if target not in ("question", "answer"):
            raise ValueError("target must be question or answer")
        if thumb not in (None, 1, -1):
            raise ValueError("thumb must be 1, -1, or null")
        session = self.engine_session(task_id)
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO answer_feedback(task_id,session_id,input_ref,target,content,thumb,comment,actor,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(task_id,input_ref,target,actor)
                   DO UPDATE SET session_id=excluded.session_id, content=excluded.content,
                                 thumb=excluded.thumb, comment=excluded.comment, updated_at=excluded.updated_at""",
                (task_id, session, input_ref, target, content, thumb, comment, actor, now, now))
            row = self._db.execute(
                "SELECT * FROM answer_feedback WHERE task_id=? AND input_ref=? AND target=? AND actor=?",
                (task_id, input_ref, target, actor)).fetchone()
            self.audit(actor, "feedback.upsert", task_id, {"feedback_id": row["id"], "target": target, "thumb": thumb, "has_comment": bool(comment)})
            self._commit()
        return dict(row)

    def _feedback_row(self, task_id: str, feedback_id: int, actor: str, admin: bool) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM answer_feedback WHERE id=? AND task_id=?", (feedback_id, task_id)).fetchone()
            if row is None:
                raise KeyError("feedback not found")
            if row["actor"] != actor and not admin:
                raise PermissionError("feedback belongs to another actor")
            return row

    def update_feedback(self, task_id: str, feedback_id: int, actor: str, *,
                        thumb: int | None = None, comment: str | None = None, admin: bool = False) -> dict:
        if thumb not in (None, 1, -1):
            raise ValueError("thumb must be 1, -1, or null")
        with self._lock:
            self._feedback_row(task_id, feedback_id, actor, admin)
            self._db.execute("UPDATE answer_feedback SET thumb=?, comment=?, updated_at=? WHERE id=?",
                             (thumb, comment, time.time(), feedback_id))
            row = self._db.execute("SELECT * FROM answer_feedback WHERE id=?", (feedback_id,)).fetchone()
            self.audit(actor, "feedback.update", task_id, {"feedback_id": feedback_id, "target": row["target"], "thumb": thumb, "has_comment": bool(comment)})
            self._commit()
        return dict(row)

    def delete_feedback(self, task_id: str, feedback_id: int, actor: str, *, admin: bool = False) -> None:
        with self._lock:
            row = self._feedback_row(task_id, feedback_id, actor, admin)
            self._db.execute("DELETE FROM answer_feedback WHERE id=?", (feedback_id,))
            self.audit(actor, "feedback.delete", task_id, {"feedback_id": feedback_id, "target": row["target"]})
            self._commit()

    def task_artifacts(self, task_id: str, *, include_stale: bool = True) -> list[dict]:
        with self._lock:
            where = "task_id=?" if include_stale else "task_id=? AND stale=0"
            return [dict(r) for r in self._db.execute(
                f"SELECT * FROM task_artifacts WHERE {where} ORDER BY generation DESC,id", (task_id,))]

    def rerun_prerequisite(self, task_id: str, from_phase: str) -> dict | None:
        """Return the first unfulfilled upstream phase, or None when rerun is valid."""
        with self._lock:
            positions = PHASE_POSITIONS
            if from_phase not in positions: raise KeyError(from_phase)
            phases = self._db.execute(
                "SELECT phase_key,label,state FROM phases WHERE task_id=? ORDER BY position", (task_id,)
            ).fetchall()
            if not phases: raise KeyError(task_id)
            for phase in phases[:positions[from_phase]]:
                if phase["state"] not in ("approved","done"): return dict(phase)
            return None

    def prepare_rerun(self, task_id: str, from_phase: str, policy: str = "archive", actor: str = "local user") -> int:
        """Policy B: stale now, retain by generation, require downstream re-approval."""
        if policy != "archive":
            raise ValueError("rerun policy is archive: downstream work is retained and marked stale")
        positions = PHASE_POSITIONS
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
            gate_phases = downstream
            if gate_phases:
                gate_marks = ",".join("?" for _ in gate_phases)
                self._db.execute(f"UPDATE gates SET is_open=0,state='idle',selected_option=NULL,edited_text=NULL,steer=NULL,revision=revision+1 "
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
        with self._lock:
            row=self._db.execute("SELECT * FROM users WHERE provider='basic' AND external_id=?",(username.strip().lower(),)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def create_session(self, session_id: str, user_id: str, expires_at: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO sessions VALUES (?,?,?,?)",(self._session_key(session_id),user_id,expires_at,time.time())); self._commit()

    def session(self, session_id: str) -> dict | None:
        # The embedded libSQL connection is shared by FastAPI's threadpool.
        # Serialize the complete execute/fetch operation with session writes;
        # concurrent cursors on one native connection can otherwise expose a
        # transient empty result even though the next lookup succeeds.
        with self._lock:
            row=self._db.execute("SELECT s.*,u.label,u.email,u.is_admin FROM sessions s JOIN users u USING(user_id) WHERE session_id=?",(self._session_key(session_id),)).fetchone()
            return dict(row) if row else None

    def rotate_session(self, old_id: str, new_id: str, expires_at: float, now: float) -> bool:
        """Single-use refresh: revoke the old handle and insert its replacement atomically."""
        with self._lock:
            old_key, new_key = self._session_key(old_id), self._session_key(new_id)
            try:
                row = self._db.execute("SELECT user_id FROM sessions WHERE session_id=? AND expires_at>?",
                                       (old_key, now)).fetchone()
                if row is None:
                    return False
                self._db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                                 (new_key, row["user_id"], expires_at, now))
                removed = self._db.execute("DELETE FROM sessions WHERE session_id=? AND expires_at>?",
                                           (old_key, now))
                if removed.rowcount != 1:
                    self._db.rollback()
                    return False
                self._commit()
                return True
            except Exception:
                self._db.rollback()
                raise

    def delete_session(self, session_id: str) -> None:
        with self._lock: self._db.execute("DELETE FROM sessions WHERE session_id=?",(self._session_key(session_id),)); self._commit()

    def user_quota(self, user_id: str) -> dict | None:
        with self._lock:
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
        try:
            with self._lock:self._db.sync()
            activity("database.sync","Turso replica synchronized",reason=reason)
        except Exception as e:activity("database.sync.failed","Turso replica sync deferred; local data remains available",reason=reason,error=f"{type(e).__name__}: {str(e)[:180]}")
    def create_basic_user(self,*args,**kwargs):
        result=super().create_basic_user(*args,**kwargs);self._safe_sync("account create");return result
    def create_session(self,*args,**kwargs):
        result=super().create_session(*args,**kwargs);self._safe_sync("session create");return result
    def rotate_session(self,*args,**kwargs):
        result=super().rotate_session(*args,**kwargs)
        if result:self._safe_sync("session refresh")
        return result
    def delete_session(self,*args,**kwargs):
        result=super().delete_session(*args,**kwargs);self._safe_sync("session revoke");return result

def create_store(database_url: str) -> Storage:
    """Select the persistence adapter in one place.

    Local paths and sqlite:/// URLs use SQLite. A future postgres:// adapter is
    registered here; no route, runner or job code changes with that deployment.
    """
    if database_url.startswith(("postgres://", "postgresql://")):
        from .postgres_store import PostgresStore
        return PostgresStore(database_url)
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
