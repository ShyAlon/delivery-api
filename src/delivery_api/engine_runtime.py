"""Resolve and preflight the Python runtime used by sibling engines."""
from __future__ import annotations
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class EngineRuntime:
    python: Path
    checked: tuple[Path, ...]

class EngineRuntimeError(RuntimeError):
    def __init__(self, *, module: str, checked: tuple[Path, ...], engine_dir: Path):
        self.module, self.checked, self.engine_dir = module, checked, engine_dir
        tried = ", ".join(str(p) for p in checked) or "no Python interpreters"
        super().__init__(f"Required module '{module}' is unavailable. Checked: {tried}")


def _candidates(engine_dir: Path) -> list[Path]:
    root = engine_dir.parent
    values = [
        os.environ.get("INTERVIEW_ENGINE_PYTHON"),
        os.environ.get("VMODEL_PYTHON"),
        engine_dir / ".venv/bin/python", engine_dir / "venv/bin/python",
        engine_dir / ".env/bin/python", engine_dir / "env/bin/python",
        root / ".venv/bin/python",
        Path(os.environ["VIRTUAL_ENV"]) / "bin/python" if os.environ.get("VIRTUAL_ENV") else None,
        Path(sys.executable), shutil.which("python3"), shutil.which("python"),
    ]
    result=[]
    for value in values:
        if not value: continue
        path=Path(value).expanduser().resolve()
        if path not in result and path.is_file() and os.access(path,os.X_OK):result.append(path)
    return result

async def resolve_engine_runtime(engine_dir: Path, required_module: str="langchain_core") -> EngineRuntime:
    checked=[]
    env={**os.environ,"PYTHONPATH":str(engine_dir/"src")+os.pathsep+os.environ.get("PYTHONPATH","")}
    for python in _candidates(engine_dir):
        checked.append(python)
        proc=await asyncio.create_subprocess_exec(str(python),"-c",f"import {required_module}",cwd=str(engine_dir),env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        if await proc.wait()==0:return EngineRuntime(python,tuple(checked))
    raise EngineRuntimeError(module=required_module,checked=tuple(checked),engine_dir=engine_dir)
