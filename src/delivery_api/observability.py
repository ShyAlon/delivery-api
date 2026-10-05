"""Canonical human-readable event logging for local UI and server activity."""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path
from .config import default_data_dir

LOG_PATH=Path(__file__).resolve().parents[2]/"run.log"
STAGE_LOG_DIR=default_data_dir()/"logs"/"stages"
STAGE_KEYS=("intent","conops","needs","srs","architecture","specs","tests","validation")
STAGE_LOG_PATHS={phase:STAGE_LOG_DIR/f"{phase}.log" for phase in STAGE_KEYS}
logger=logging.getLogger("delivery_api.activity")
if not logger.handlers:
    logger.setLevel(logging.INFO);logger.propagate=False
    formatter=logging.Formatter("%(message)s")
    stream=logging.StreamHandler();stream.setFormatter(formatter);logger.addHandler(stream)
    try:
        file=logging.FileHandler(LOG_PATH,encoding="utf-8");file.setFormatter(formatter);logger.addHandler(file)
    except OSError:pass

def _line(kind:str,message:str,fields:dict)->str:
    stamp=datetime.now().astimezone().isoformat(timespec="milliseconds")
    safe=" ".join(f"{k}={str(v).replace(chr(10),' ')!r}" for k,v in fields.items() if v is not None)
    return f"{stamp} | {kind:<18} | {message}{' | '+safe if safe else ''}"


def stage_log_paths()->dict[str,Path]:
    STAGE_LOG_DIR.mkdir(parents=True,exist_ok=True)
    return STAGE_LOG_PATHS


def event(kind:str,message:str,**fields)->None:
    line=_line(kind,message,fields)
    logger.info(line)
    phase=fields.get("phase_key") or fields.get("phase") or fields.get("stage")
    path=STAGE_LOG_PATHS.get(str(phase)) if phase else None
    if path:
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open("a",encoding="utf-8") as handle:handle.write(line+"\n")
        except OSError:pass
