"""Run state + link tokens + usage ledger. sqlite for the demo skeleton;
the interface is the seam where Supabase slots in (free tier, 500MB).
One active run per link (scoping doc: abuse protection)."""
from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path

from vmodel_contracts import RunState

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
  token TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  founder_label TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  submission TEXT,
  session_ref TEXT,
  cost_usd REAL NOT NULL DEFAULT 0,
  budget_usd REAL NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  record TEXT NOT NULL,          -- StageTokenUsage JSON
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,            -- intent_model | bundle | verification_report | runner
  ref TEXT NOT NULL,             -- path or URL
  ts REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True) if "/" in path else None
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def create_link(self, founder_label: str) -> dict:
        token = secrets.token_urlsafe(24)
        run_id = secrets.token_hex(8)
        self.db.execute("INSERT INTO links VALUES (?,?,?,1,?)", (token, run_id, founder_label, time.time()))
        self.db.execute("INSERT INTO runs (run_id,state,budget_usd,created_at,updated_at) VALUES (?,?,?,?,?)",
                        (run_id, RunState.CREATED.value, 6.0, time.time(), time.time()))
        self.db.commit()
        return {"token": token, "run_id": run_id}

    def link(self, token: str) -> dict | None:
        row = self.db.execute("SELECT * FROM links WHERE token=? AND active=1", (token,)).fetchone()
        return dict(row) if row else None

    def deactivate_link(self, token: str) -> None:
        self.db.execute("UPDATE links SET active=0 WHERE token=?", (token,))
        self.db.commit()

    def set_state(self, run_id: str, state: RunState) -> None:
        self.db.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?", (state.value, time.time(), run_id))
        self.db.commit()

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def add_usage(self, run_id: str, record_json: str, cost_usd: float) -> float:
        """Ledger write + run total. Returns the new run total."""
        self.db.execute("INSERT INTO usage (run_id,record,ts) VALUES (?,?,?)", (run_id, record_json, time.time()))
        self.db.execute("UPDATE runs SET cost_usd=cost_usd+?, updated_at=? WHERE run_id=?",
                        (cost_usd, time.time(), run_id))
        self.db.commit()
        return self.run(run_id)["cost_usd"]

    def total_spend(self) -> float:
        return self.db.execute("SELECT COALESCE(SUM(cost_usd),0) t FROM runs").fetchone()["t"]

    def add_artifact(self, run_id: str, kind: str, ref: str) -> None:
        self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (run_id, kind, ref, time.time()))
        self.db.commit()

    def artifacts(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM artifacts WHERE run_id=?", (run_id,))]
