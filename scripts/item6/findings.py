"""One findings list for a run: warnings and errors that did not need a stop. Append only, best effort.

Each line is JSON with an ISO time, a kind and the text. The file lives in the run's evidence directory
(/evidence/run/findings.jsonl) when it is writable; otherwise the entry is only printed by the caller.
A failure to write a finding never stops a run."""
import datetime,json,os
from pathlib import Path

DEFAULT='/evidence/run/findings.jsonl'


def record(kind,text,path=None):
    path=Path(path or os.environ.get('ITEM6_FINDINGS') or DEFAULT)
    entry={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),'kind':str(kind)[:60],'text':str(text)[:1000]}
    try:
        if not path.parent.is_dir():return False
        fd=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        with os.fdopen(fd,'a') as f:f.write(json.dumps(entry,sort_keys=True)+'\n')
        return True
    except OSError:return False
