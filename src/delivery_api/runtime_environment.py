"""Drop retired settings after dotenv loading, before any PG-only entry point.

Only variable names may be logged. Never infer a DB target or alter PG/auth keys.
"""
import os
import sys


def neutralize_retired_storage(environ=None,*,report=None):
    environ=os.environ if environ is None else environ
    report=report or (lambda line:print(line,file=sys.stderr))
    names=sorted({'TURSO_DATABASE_URL','TURSO_AUTH_TOKEN','TURSO_SYNC_INTERVAL'}|
        {name for name in environ if name.startswith('TURSO_')})
    ignored=[]
    for name in names:
        if environ.get(name):
            report('Ignoring retired PG-only setting: '+name)
            ignored.append(name)
        # Empty, not removed: later load_dotenv(override=False) cannot revive it.
        environ[name]=''
    return tuple(ignored)
