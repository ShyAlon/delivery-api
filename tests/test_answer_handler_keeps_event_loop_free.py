"""The answer handler is async; a database call made directly in it blocks the whole event loop for a round trip
(~440-480 ms at the founder's ~160 ms database distance: the 11-10 loop.stall.stack rows at app.py 1346/1427).
Every store/runner call in it must go through run_in_threadpool, like the engine submit already does."""
import ast
import pathlib

APP=pathlib.Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'app.py'


def _handler():
    tree=ast.parse(APP.read_text())
    return next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='answer_interview')


def _blocking_calls(fn):
    allowed=set()
    for node in ast.walk(fn):
        if isinstance(node,ast.Call) and getattr(node.func,'id',None)=='run_in_threadpool':
            for arg in node.args[:1]:allowed.add(id(arg))
    awaited={id(n.value) for n in ast.walk(fn) if isinstance(n,ast.Await)}   # coroutines (runner.send_input) yield the loop
    found=[]
    for node in ast.walk(fn):
        if isinstance(node,ast.Call) and id(node) not in awaited:
            f=node.func
            name=None
            if isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.value.id in ('store','runner'):name=f.value.id+'.'+f.attr
            elif isinstance(f,ast.Name) and f.id=='get_task':name='get_task'
            if name:found.append((node.lineno,name))
    # a reference passed as run_in_threadpool's first argument is an Attribute/Name, not a Call, so any Call here blocks
    return found


def test_no_direct_store_or_runner_call_in_the_async_answer_handler():
    direct=_blocking_calls(_handler())
    assert not direct,f'blocking calls on the event loop: {direct}'


def test_the_handler_still_offloads_them():
    src=ast.get_source_segment(APP.read_text(),_handler())
    assert src.count('run_in_threadpool(store.add_event_batch,')==2 and 'run_in_threadpool(store.add_event,' in src and 'run_in_threadpool(store.task,' in src
