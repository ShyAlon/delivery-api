"""Shape-only child failure envelope. No exception values, source or locals."""
import json,re,sys,traceback
from pathlib import Path

PREFIX=b'REQUI_CHILD_FAILURE '


def project(error):
    frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
            for f in traceback.extract_tb(error.__traceback__)[-12:]]
    row={'error_class':type(error).__name__,'frames':frames}
    causes=[];seen={id(error)};current=error
    while len(causes)<6:
        current=current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if current is None or id(current) in seen:break
        seen.add(id(current));causes.append(type(current).__name__)
    if causes:row['cause_classes']=causes
    return row


def emit(error):
    sys.stderr.write(PREFIX.decode()+json.dumps(project(error),separators=(',',':'))+'\n')
    sys.stderr.flush()


def parse(line):
    if not line.startswith(PREFIX):return None
    try:
        row=json.loads(line[len(PREFIX):])
        atom=lambda v:isinstance(v,str) and bool(re.fullmatch(r'[A-Za-z0-9_.<>-]{1,100}',v))
        if set(row) not in ({'error_class','frames'},{'error_class','frames','cause_classes'}) or not atom(row['error_class']):return None
        if 'cause_classes' in row and (not isinstance(row['cause_classes'],list) or len(row['cause_classes'])>6 or not all(atom(v) for v in row['cause_classes'])):return None
        if not isinstance(row['frames'],list) or len(row['frames'])>12:return None
        for f in row['frames']:
            if set(f)!={'file','function','line'} or not atom(f['file']) or not atom(f['function']):return None
            if type(f['line']) is not int or not 0<f['line']<1000000:return None
        return row
    except (ValueError,TypeError,KeyError,AttributeError):return None
