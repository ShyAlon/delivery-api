"""Pure reviewed child argv/input validation. No parent runtime config imports."""
import hashlib,json
from pathlib import Path


def manifest(argv,root,documents=None):
    root=Path(root).resolve();files={}
    if not argv or not isinstance(argv,list) or any(not isinstance(v,str) for v in argv):
        raise PermissionError('typed generator argv required')
    # Match only the reviewed orchestrator command shape. Never allow another
    # CLI mode to add a path, export, replay directory or executable side effect.
    valued={'--conops','--system-baseline','--out','--to','--from'}
    flags={'--require-decisions','--review','--final-review'}
    seen=set();index=1
    while index<len(argv):
        arg=argv[index]
        if arg in seen:raise PermissionError('duplicate generator option')
        seen.add(arg)
        if arg in valued:
            if index+1>=len(argv):raise PermissionError('generator option value missing')
            if arg in ('--to','--from') and argv[index+1] not in ('needs','srs','architecture','items','tests','validation'):
                raise PermissionError('reviewed generator stage required')
            index+=2
        elif arg in flags:index+=1
        else:raise PermissionError('unreviewed child generator option')
    if '--final-review' in seen and ('--to' not in argv or argv[argv.index('--to')+1]!='validation' or ('--from' in seen and argv[argv.index('--from')+1]!='validation')):
        raise PermissionError('final review requires bounded validation without rewind')
    if documents is None:raise PermissionError('owned generator documents required')
    # Only the CLI's explicit inputs, never output/checkpoint files.
    paths=[argv[0]]+[argv[i+1] for i,arg in enumerate(argv[:-1]) if arg in ('--conops','--system-baseline')]
    for path in paths:
        value=Path(path)
        if not value.is_absolute() or value.resolve()!=value or not value.is_relative_to(root):
            raise PermissionError('generator input must be a canonical owned file')
        key=str(value.relative_to(root))
        raw=documents.read(key)
        if raw is None:raise PermissionError('canonical owned generator input missing')
        files[key]=hashlib.sha256(raw).hexdigest()
    if '--out' not in argv or Path(argv[argv.index('--out')+1])!=root/'spec':raise PermissionError('owned generator output required')
    value={'argv':argv,'files':files}
    return value,hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

