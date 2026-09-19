"""Resolve, validate and persist the Python runtime used by sibling engines."""
from __future__ import annotations
import asyncio,os,shutil,sys
from dataclasses import dataclass
from pathlib import Path
SETTINGS_FILE=Path(__file__).resolve().parents[2]/'.engine-python'
@dataclass(frozen=True)
class EngineRuntime: python:Path; checked:tuple[Path,...]
class EngineRuntimeError(RuntimeError):
 def __init__(self,*,module:str,checked:tuple[Path,...],engine_dir:Path):
  self.module,self.checked,self.engine_dir=module,checked,engine_dir
  super().__init__(f"Required module '{module}' is unavailable. Checked: {', '.join(map(str,checked)) or 'no Python interpreters'}")
def saved_python()->Path|None:
 try:value=SETTINGS_FILE.read_text().strip();return Path(value).expanduser().resolve() if value else None
 except OSError:return None
def _candidates(engine_dir:Path)->list[Path]:
 root=engine_dir.parent; values=[os.environ.get('INTERVIEW_ENGINE_PYTHON'),os.environ.get('VMODEL_PYTHON'),saved_python(),engine_dir/'.venv/bin/python',engine_dir/'venv/bin/python',engine_dir/'.env/bin/python',engine_dir/'env/bin/python',root/'.venv/bin/python',Path(os.environ['VIRTUAL_ENV'])/'bin/python' if os.environ.get('VIRTUAL_ENV') else None]
 for venv in sorted(root.glob('*/.venv'))+sorted(root.glob('*/venv')):values.append(venv/'bin/python')
 values += [Path(sys.executable),shutil.which('python3'),shutil.which('python')]
 result=[]
 for value in values:
  if not value:continue
  path=Path(value).expanduser().resolve()
  if path not in result and path.is_file() and os.access(path,os.X_OK):result.append(path)
 return result
async def validate_python(python:Path,module:str='langchain_core')->bool:
 if not python.is_file() or not os.access(python,os.X_OK):return False
 proc=await asyncio.create_subprocess_exec(str(python),'-c',f'import {module}',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
 return await proc.wait()==0
async def discover_engine_runtimes(engine_dir:Path)->list[Path]:
 return [p for p in _candidates(engine_dir) if await validate_python(p)]
async def save_python(value:str,engine_dir:Path)->Path:
 path=Path(value).expanduser().resolve()
 if not await validate_python(path):raise ValueError(f"{path} cannot import langchain_core")
 SETTINGS_FILE.write_text(str(path)+'\n');return path
async def resolve_engine_runtime(engine_dir:Path,required_module:str='langchain_core')->EngineRuntime:
 checked=[]
 for python in _candidates(engine_dir):
  checked.append(python)
  if await validate_python(python,required_module):return EngineRuntime(python,tuple(checked))
 raise EngineRuntimeError(module=required_module,checked=tuple(checked),engine_dir=engine_dir)
