"""Shape-only child failure envelope. No exception values, source or locals."""
import json,re,sys,traceback
from pathlib import Path

PREFIX=b'REQUI_CHILD_FAILURE '


def emit(error):
    frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
            for f in traceback.extract_tb(error.__traceback__)[-12:]]
    row={'error_class':type(error).__name__,'frames':frames}
    sys.stderr.write(PREFIX.decode()+json.dumps(row,separators=(',',':'))+'\n')
    sys.stderr.flush()


def parse(line):
    if not line.startswith(PREFIX):return None
    try:
        row=json.loads(line[len(PREFIX):])
        atom=lambda v:isinstance(v,str) and bool(re.fullmatch(r'[A-Za-z0-9_.<>-]{1,100}',v))
        if set(row)!={'error_class','frames'} or not atom(row['error_class']):return None
        if not isinstance(row['frames'],list) or len(row['frames'])>12:return None
        for f in row['frames']:
            if set(f)!={'file','function','line'} or not atom(f['file']) or not atom(f['function']):return None
            if type(f['line']) is not int or not 0<f['line']<1000000:return None
        return row
    except (ValueError,TypeError,KeyError,AttributeError):return None
