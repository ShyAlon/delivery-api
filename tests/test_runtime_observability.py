"""Runtime observability bundle: quiet console, slow-request evidence, image build time, UI action logging wiring, and one-events-read-per task poll.

Hermetic: no network, no Docker, no browser. Node tests skip without node.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "delivery_api" / "web"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(source):
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


# --- quiet console -------------------------------------------------------

def test_routine_activity_is_file_only_when_marked(caplog):
    from delivery_api import observability
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            if getattr(record, 'console', True):
                records.append(record)
    handler = Capture()
    observability.logger.addHandler(handler)
    try:
        observability.event("http.request", "Request completed", console=False, path='/api/tasks/x', status=200)
        observability.event("http.request", "Request completed", level=logging.WARNING, console=True, path='/api/tasks/x', status=200)
    finally:
        observability.logger.removeHandler(handler)
    assert len(records) == 1 and "Request completed" in records[0].getMessage()


def test_request_log_middleware_quiets_routine_and_warns_on_slow():
    src = (ROOT / "src" / "delivery_api" / "app.py").read_text()
    assert re.search(r"SLOW_REQUEST_MS\s*=\s*1000", src)
    line = re.search(r'activity\("http\.request".*', src).group(0)
    assert "console=_noteworthy" in line and "db_queries=stats.queries" in line and "db_ms=" in line
    assert "_noteworthy=response.status_code>=400 or duration_ms>=SLOW_REQUEST_MS" in src


def test_uvicorn_access_log_disabled():
    src = (ROOT / "src" / "delivery_api" / "__main__.py").read_text()
    assert "access_log=False" in src


# --- image build time -----------------------------------------------------

def test_image_build_time_baked_and_surfaced():
    dockerfile = (ROOT / "Dockerfile.full").read_text()
    assert "ARG REQUI_BUILD_AT=" in dockerfile and "ENV REQUI_BUILD_AT=$REQUI_BUILD_AT" in dockerfile
    for script in ("build-local-image.sh", "build-full-image.sh"):
        text = (ROOT / "scripts" / "docker" / script).read_text()
        assert "--build-arg REQUI_BUILD_AT=" in text and "date -u" in text
    from delivery_api import app as module
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    import os
    stamp="2026-10-27T10:00:00Z"
    with patch.dict(os.environ, {"REQUI_BUILD_ID":"test-build","REQUI_BUILD_AT":stamp}):
        runtime=module._runtime_version()
    assert runtime['built_at']==stamp
    with patch.object(module,'RUNTIME_VERSION',runtime), patch.object(module.config,'CLOUD_TENANTS',True):
        assert TestClient(module.app).get('/health').json()['built_at']==stamp


# --- one events read per task GET -----------------------------------------

def test_task_get_reads_events_once(monkeypatch,pg_workspace_factory,pg_task_app):
    from fastapi.testclient import TestClient
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as workspace,pg_workspace_factory() as other:
        store=workspace.store
        assert isinstance(store,PostgresStore)
        with tenant_context(workspace.scope):
            task=store.create_task('Observed task')
        calls=[]
        real_snapshot=store.task_snapshot
        def counting(task_id):
            calls.append(task_id)
            return real_snapshot(task_id)
        monkeypatch.setattr(store,'task_snapshot',counting)
        client=TestClient(pg_task_app(workspace))
        path=f"/api/tasks/{task['task_id']}"
        reply=client.get(path,headers=workspace.headers)
        assert reply.status_code==200,reply.text
        assert calls==[task['task_id']],f'expected one combined task/events read, got {calls}'
        assert reply.json()['events']==[]
        calls.clear()
        assert client.get(path).status_code==401
        assert calls==[],'anonymous request reached the task snapshot'
        assert client.get(path,headers=other.headers).status_code==404
        assert calls==[task['task_id']],'foreign request must still use the scoped snapshot'


# --- UI action logging ------------------------------------------------------

def test_ui_action_module_is_preloaded_and_wired():
    index = (WEB / "index.html").read_text()
    assert '/assets/ui-action.js' in index
    checks = {
        "account-component.js": ["actionBusy('auth.'+mode", "actionIdle('auth.'+mode", "logEnter('auth.'+mode"],
        "task.js": ["runAction('engine.start'", "guardBlocked('engine.answer'",
                    "disabledSet('engine.answer'", "disabledClear('engine.answer'", "logEnter('answer-composer'"],
        "dashboard.js": ["runAction('task.create'"],
        "taskactions.js": ["runAction('task.rerun'", "runAction('task.rerun.preview'"],
        "shared-dialog.js": ["logEnter('dialog','form.requestSubmit')", "logEnter('dialog','primary.click')", "logEnter('dialog','none"],
    }
    for name, needles in checks.items():
        text = (WEB / name).read_text()
        assert "from './ui-action.js'" in text, f"{name} must import ui-action.js"
        for needle in needles:
            assert needle in text, f"{name} missing {needle}"


@needs_node
def test_ui_action_helpers_log_only_run_action_owns_disabled():
    src = """
globalThis.location={pathname:'/tasks/x/view'};
globalThis.fetch=()=>Promise.resolve({ok:true});
globalThis.window=globalThis;
globalThis.addEventListener=()=>{};
globalThis.document={querySelector:()=>null,addEventListener:()=>{}};
const lines=[];const orig=console.info;console.info=(...a)=>lines.push(a.map(String).join(' '));
const m=await import('%s');
const button={disabled:false};
m.actionBusy('engine.answer',button);
if(button.disabled!==false)throw new Error('actionBusy must be log-only; DOM effects belong to runAction alone');
m.actionIdle('engine.answer',button,'settled');
if(button.disabled!==false)throw new Error('actionIdle must be log-only; DOM effects belong to runAction alone');
m.guardBlocked('engine.answer','already-sending');
m.disabledSet('engine.answer','all-controls');
m.disabledClear('engine.answer','textarea+submit');
m.logEnter('answer-composer','form.requestSubmit');
console.info=orig;
process.stdout.write(JSON.stringify(lines));
""" % (WEB / "ui-action.js")
    import json
    lines = json.loads(_node(src))
    text = "\n".join(lines)
    for needle in ("ui.inflight", "engine.answer busy", "idle (settled)", "blocked: already-sending",
                   "ui.disabled", "ui.enter", "answer-composer: Enter -> form.requestSubmit"):
        assert needle in text, f"missing log line {needle!r} in {text!r}"


@needs_node
def test_run_action_is_the_shared_inflight_guard():
    src = """
globalThis.location={pathname:'/tasks/x/view'};
globalThis.fetch=()=>Promise.resolve({ok:true});
globalThis.window=globalThis;
globalThis.addEventListener=()=>{};
globalThis.document={querySelector:()=>null,addEventListener:()=>{}};
console.info=()=>{};
const m=await import('%s');
if(typeof m.runAction!=='function')throw new Error('runAction not exported');
const button={disabled:false,textContent:'Create empty task',title:'',classList:{add(c){this[c]=true},remove(c){delete this[c]}},setAttribute(k,v){this['attr_'+k]=v},removeAttribute(k){delete this['attr_'+k]}};
let resolveCall;
const slow=()=>new Promise(r=>{resolveCall=r});
// Busy state must be a synchronous DOM effect, not just a log line.
const pending=m.runAction('task.create',button,slow,{busyLabel:'Creating…',reason:'Creating the task'});
if(button.disabled!==true)throw new Error('triggering button not disabled');
if(button['attr_aria-busy']!=='true')throw new Error('aria-busy not set');
if(button.classList['is-busy']!==true)throw new Error('is-busy class not set');
if(button.textContent!=='Creating…')throw new Error('busy label not shown');
if(!button.title)throw new Error('disabled reason not visible (title empty)');
// Re-entry while in flight is blocked and does not double-run.
let ran=0;
const again=await m.runAction('task.create',button,async()=>{ran++});
if(ran!==0||again!==undefined)throw new Error('in-flight re-entry was not blocked');
resolveCall('done');
const out=await pending;
if(out!=='done')throw new Error('runAction did not return the fn result');
if(button.disabled!==false)throw new Error('button not re-enabled on success');
if(button.textContent!=='Create empty task')throw new Error('label not restored');
// Error path: re-enables and rethrows.
let threw=false;
try{await m.runAction('task.create',button,async()=>{throw new Error('boom')},{busyLabel:'Creating…'})}catch(e){threw=e.message==='boom'}
if(!threw)throw new Error('error not rethrown');
if(button.disabled!==false)throw new Error('button not re-enabled on error');
// keepDisabledOnSuccess: stays down until the page re-renders.
await m.runAction('engine.answer',button,async()=>{}, {keepDisabledOnSuccess:true});
if(button.disabled!==true)throw new Error('keepDisabledOnSuccess did not hold');
// Restore returns the button to its PRE-ACTION state, not blindly to enabled.
button.disabled=false;
const preDisabled={disabled:true,textContent:'Stop',title:'',classList:{add(c){this[c]=true},remove(c){delete this[c]}},setAttribute(k,v){this['attr_'+k]=v},removeAttribute(k){delete this['attr_'+k]}};
await m.runAction('engine.stop',preDisabled,async()=>{});
if(preDisabled.disabled!==true)throw new Error('runAction must restore the prior disabled state, not force-enable');
process.stdout.write('ok');
""" % (WEB / "ui-action.js")
    assert _node(src).strip() == "ok"


def test_pool_budget_leaves_room_for_engine_sessions():
    # Guard for the 2026-10-26 EMAXCONNSESSION incident: the Supabase pooler
    # (session mode) allows 15 clients. After the 2026-10-26 pool redesign the
    # whole delivery process shares ONE pool of 10 (delivery-api owns it and injects
    # it into the in-process engine), so the budget is the pool alone with
    # headroom for non-pool clients (migrations, ops psql, the engine
    # heartbeat's own bookkeeping on standalone).
    from delivery_api import db_pool, postgres_scope
    assert db_pool.PROCESS_POOL_MAX == 10
    assert postgres_scope.POOL_OPEN_MAX == db_pool.PROCESS_POOL_MAX
    assert db_pool.PROCESS_POOL_MAX <= 15 - 5


def test_remaining_server_action_surfaces_share_the_guard():
    expected={
        'purchase.js':['purchase.pay','purchase.check','purchase.receipt'],
        'gate.js':['gate.decide','gate.comment','gate.draft','gate.toggle','gate.semantic','gate.stack','gate.revision','task.replay'],
        'decisions.js':['decision.apply','decision.comment','decision.draft'],
        'qa.js':['feedback:'],
        'completion.js':['package.prepare'],
        'ops.js':['ops.grant','ops.refresh','ops.logs'],
        'dialogs.js':['requirement.resolve','founder.decision'],
    }
    for file,actions in expected.items():
        source=(WEB/file).read_text()
        assert 'runAction' in source,file
        for action in actions:assert action in source,(file,action)


@needs_node
def test_guard_does_not_flatten_dom_when_no_busy_label_was_requested():
    src=r"""
import fs from 'node:fs';
const code=fs.readFileSync(%r,'utf8').replace("import {clientLog} from './client-core.js';","const clientLog=()=>{};");
const m=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
let writes=0;
const b={disabled:false,title:'',get textContent(){return 'nested label'},set textContent(x){writes++},classList:{add(){},remove(){}},setAttribute(){},removeAttribute(){}};
await m.runAction('test',b,async()=>{});
if(writes!==0)throw Error('Guard flattened caller-owned spinner or button content');
""" % str(WEB/'ui-action.js')
    _node(src)
