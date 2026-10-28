"""Canonical human-readable event logging for local UI and server activity."""
from __future__ import annotations
import logging
import threading
from logging.handlers import RotatingFileHandler
from .log_safety import safe_text,safe_fields
from datetime import datetime
from pathlib import Path
from .config import default_data_dir
from .log_durability import degraded
from .log_budget import BUDGET

LOG_PATH=Path(__file__).resolve().parents[2]/"run.log"
STAGE_LOG_DIR=default_data_dir()/"logs"/"stages"
STAGE_KEYS=("intent","conops","needs","srs","architecture","specs","tests","validation")
STAGE_LOG_PATHS={phase:STAGE_LOG_DIR/f"{phase}.log" for phase in STAGE_KEYS}
class _EvidenceFileHandler(RotatingFileHandler):
    def handleError(self, record):
        import sys
        degraded('activity-file', sys.exc_info()[1] or OSError())


logger=logging.getLogger("delivery_api.activity")
if not logger.handlers:
    logger.setLevel(logging.INFO);logger.propagate=False
    formatter=logging.Formatter("%(message)s")
    stream=logging.StreamHandler();stream.setFormatter(formatter)
    # Console stays readable: call sites mark routine records console=False;
    # the file handler below is unfiltered, so Debug evidence (run.log -> ZIP)
    # always keeps every line.
    class _ConsoleFilter(logging.Filter):
        def filter(self,record):return getattr(record,'console',True)
    stream.addFilter(_ConsoleFilter());logger.addHandler(stream)
    try:
        file=_EvidenceFileHandler(LOG_PATH,maxBytes=BUDGET['activity'][0],backupCount=BUDGET['activity'][1]-1,encoding="utf-8");file.setFormatter(formatter);logger.addHandler(file)
    except OSError as error:degraded('activity-file',error)

_stage_log_lock=threading.RLock()

def _line(kind:str,message:str,fields:dict)->str:
    stamp=datetime.now().astimezone().isoformat(timespec="milliseconds")
    kind=safe_text(kind,80);message=safe_text(message)
    fields=safe_fields(fields)
    safe=" ".join(f"{k}={str(v).replace(chr(10),' ')!r}" for k,v in fields.items() if v is not None)
    return f"{stamp} | {kind:<18} | {message}{' | '+safe if safe else ''}"


def stage_log_paths()->dict[str,Path]:
    STAGE_LOG_DIR.mkdir(parents=True,exist_ok=True)
    return STAGE_LOG_PATHS


def event(kind:str,message:str,*,level:int=logging.INFO,console:bool=True,**fields)->None:
    from .diagnostic_evidence import context
    fields={**context.get(),**fields}
    line=_line(kind,message,fields)
    logger.log(level,line,extra={'console':console})
    phase=fields.get("phase_key") or fields.get("phase") or fields.get("stage")
    path=STAGE_LOG_PATHS.get(str(phase)) if phase else None
    if path:
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            # No persistent handler cache across stage changes. Bounded files.
            with _stage_log_lock:
                handler=_StageFileHandler(path,maxBytes=BUDGET['stages'][0],backupCount=BUDGET['stages'][1]//len(STAGE_KEYS)-1,encoding='utf-8')
                try:handler.emit(logging.LogRecord('stage',logging.INFO,'',0,line,(),None))
                finally:handler.close()
        except OSError as error:degraded('stage-file:'+str(phase),error)


class _StageFileHandler(RotatingFileHandler):
    def handleError(self, record):
        import sys
        key = Path(self.baseFilename).stem
        degraded('stage-file:'+key, sys.exc_info()[1] or OSError())
