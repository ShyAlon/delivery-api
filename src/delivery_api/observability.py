"""Canonical human-readable event logging for local UI and server activity."""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

LOG_PATH=Path(__file__).resolve().parents[2]/"run.log"
logger=logging.getLogger("delivery_api.activity")
if not logger.handlers:
    logger.setLevel(logging.INFO);logger.propagate=False
    formatter=logging.Formatter("%(message)s")
    stream=logging.StreamHandler();stream.setFormatter(formatter);logger.addHandler(stream)
    try:
        file=logging.FileHandler(LOG_PATH,encoding="utf-8");file.setFormatter(formatter);logger.addHandler(file)
    except OSError:pass

def event(kind:str,message:str,**fields)->None:
    stamp=datetime.now().astimezone().isoformat(timespec="milliseconds")
    safe=" ".join(f"{k}={str(v).replace(chr(10),' ')!r}" for k,v in fields.items() if v is not None)
    logger.info("%s | %-18s | %s%s",stamp,kind,message,f" | {safe}" if safe else "")
