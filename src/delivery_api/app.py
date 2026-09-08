"""delivery-api HTTP surface. Links, not accounts (scoping doc Decision 5).

Flow: Shy mints a personal link per tester -> tester lands in the interview ->
intent review gate -> generation job -> bundle delivery -> verification job ->
verdict report. Interview/generation/verification run in their own repos;
this service owns state, tokens, storage, budgets, and the cost rollup.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from vmodel_contracts import RunState, RunStatus

from . import config
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from .jobs import JobQueue
from .store import Store

app = FastAPI(title="delivery-api", version="0.1.0")
store = Store(config.DB_PATH)
jobs = JobQueue(store)


@app.on_event("startup")
async def _start():
    await jobs.start()


@app.on_event("shutdown")
async def _stop():
    await jobs.stop()


def _admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(403, "link creation is admin-only")


class LinkRequest(BaseModel):
    founder_label: str


class StartRequest(BaseModel):
    token: str
    free_text: str
    known_answers: dict[str, str] = {}


@app.post("/links", dependencies=[Depends(_admin)])
def create_link(req: LinkRequest):
    """One personal link per tester. No signup; the token gates the run."""
    if not check_global_budget(store.total_spend(), config.GLOBAL_BUDGET_USD):
        raise HTTPException(402, "global spend cap reached; no new runs")
    link = store.create_link(req.founder_label)
    return {"url": f"/runs/start?token={link['token']}", **link}


@app.post("/runs")
def start_run(req: StartRequest):
    link = store.link(req.token)
    if not link:
        raise HTTPException(403, "unknown or spent link")
    run = store.run(link["run_id"])
    if run["state"] != RunState.CREATED.value:
        raise HTTPException(409, f"run already {run['state']}")  # one active run per link
    store.set_state(link["run_id"], RunState.IN_INTERVIEW)
    # Interview engine drives from here via its own service in the wired
    # deployment; the skeleton records the submission and state.
    store.db.execute("UPDATE runs SET submission=? WHERE run_id=?",
                     (req.model_dump_json(), link["run_id"]))
    store.db.commit()
    return {"run_id": link["run_id"], "state": RunState.IN_INTERVIEW.value}


@app.get("/runs/{run_id}", response_model=RunStatus)
def run_status(run_id: str):
    run = store.run(run_id)
    if not run:
        raise HTTPException(404)
    return RunStatus(run_id=run_id, state=RunState(run["state"]),
                     token_budget_usd=run["budget_usd"])


class UsageRecord(BaseModel):
    record_json: str
    cost_usd: float


@app.post("/runs/{run_id}/usage")
def add_usage(run_id: str, rec: UsageRecord):
    """Modules report every LLM call here. The hard stop lives HERE: a stage
    that would cross the cap kills the run before spending further."""
    run = store.run(run_id)
    if not run:
        raise HTTPException(404)
    total = store.add_usage(run_id, rec.record_json, rec.cost_usd)
    try:
        check_run_budget(total, run["budget_usd"], run_id)
    except BudgetExceeded:
        store.set_state(run_id, RunState.BUDGET_STOPPED)
        raise HTTPException(402, f"budget hard stop at ${total:.2f}")
    return {"run_total_usd": total}


@app.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str):
    return store.artifacts(run_id)


@app.get("/health")
def health():
    return {"ok": True, "total_spend_usd": store.total_spend()}
