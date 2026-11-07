#!/usr/bin/env python3
"""Fail when the HTTP interview control path branches on engine prose.

The control path is the three interview routes, matched by decorator so
helper renames cannot silently break the check: the string-marker version
went stale eight days after creation when a refactor removed the helper it
sliced to, and with no CI wiring nobody noticed for weeks. A route that is
missing or re-routed is a loud error, never a silent pass.

engine_client.py and engine_projection.py are scanned whole: no banned token
may appear there at all.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANNED = ('REQUIRE_EVENT', '[y/N]', 'You:', 'Approve this intent model?')
ROUTES = (
    ('post', '/api/tasks/{task_id}/start'),
    ('post', '/api/tasks/{task_id}/answer'),
    ('get', '/api/tasks/{task_id}'),
)
WHOLE_FILES = ('src/delivery_api/engine_client.py', 'src/delivery_api/engine_projection.py')


def route_functions(tree):
    """Module-level functions whose decorator is one of the control routes."""
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            func = dec.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == 'app'):
                continue
            path_arg = dec.args[0]
            if isinstance(path_arg, ast.Constant) and (func.attr, path_arg.value) in ROUTES:
                found[(func.attr, path_arg.value)] = node
    return found


def branch_violations(node, where):
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.IfExp, ast.While)):
            branch = ast.unparse(sub.test)
            if any(x in branch for x in BANNED):
                out.append(f'{where}:{sub.lineno}: prose controls process: {branch}')
    return out


def check(app_path=ROOT / 'src/delivery_api/app.py',
          whole=tuple(ROOT / p for p in WHOLE_FILES)):
    errors = []
    tree = ast.parse(Path(app_path).read_text(), filename=str(app_path))
    found = route_functions(tree)
    missing = [f'{method.upper()} {path}' for method, path in ROUTES if (method, path) not in found]
    if missing:
        errors.append('control route missing or re-routed: ' + ', '.join(missing))
    for node in found.values():
        errors.extend(branch_violations(node, f'{Path(app_path).name}:{node.name}'))
    for path in whole:
        text = Path(path).read_text()
        for token in BANNED:
            if token in text:
                errors.append(f'{path}: HTTP control path contains banned token {token!r}')
    return errors


if __name__ == '__main__':
    failures = check()
    if failures:
        print('\n'.join(failures))
        sys.exit(1)
    print('process-control KPI passed: enum/number/ID control only')
