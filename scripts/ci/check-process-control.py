#!/usr/bin/env python3
"""Fail when the HTTP interview control path branches on engine prose."""
import ast,sys
from pathlib import Path
BANNED=('REQUIRE_EVENT','[y/N]','You:','Approve this intent model?')
app=Path('src/delivery_api/app.py').read_text()
ranges=[]
for marker,end_marker in [('@app.post("/api/tasks/{task_id}/start")','class RequirementResolution'),('@app.post("/api/tasks/{task_id}/answer")','def _task_ui_state'),('@app.get("/api/tasks/{task_id}")','@app.get("/api/tasks/{task_id}/events")')]:
 start=app.index(marker);end=app.index(end_marker,start);ranges.append(app[start:end])
texts={Path('src/delivery_api/engine_client.py'):Path('src/delivery_api/engine_client.py').read_text(),Path('src/delivery_api/engine_projection.py'):Path('src/delivery_api/engine_projection.py').read_text(),Path('src/delivery_api/app.py'):'\n'.join(ranges)}
errors=[]
for path,text in texts.items():
 tree=ast.parse(text,filename=str(path))
 for node in ast.walk(tree):
  if isinstance(node,(ast.If,ast.IfExp,ast.While)):
   branch=ast.unparse(node.test)
   if any(x in branch for x in BANNED):errors.append(f'{path}:{node.lineno}: prose controls process: {branch}')
 if path.name != 'app.py':
  for token in BANNED:
   if token in text:errors.append(f'{path}: HTTP control path contains banned token {token!r}')
if errors:print('\n'.join(errors));sys.exit(1)
print('process-control KPI passed: enum/number/ID control only')
