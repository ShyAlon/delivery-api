"""Read existing owner settings as values, never execute or rewrite them."""
from pathlib import Path
import re
from urllib.parse import urlsplit


def validate(path: Path) -> dict[str,str]:
    if not path.is_file() or path.stat().st_mode&0o077:
        raise ValueError('existing private settings file required (mode 600)')
    if path.stat().st_size>262144:raise ValueError('settings file too large')
    result={}
    for raw in path.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or key in result or '\0' in value:
            raise ValueError('settings contain malformed or duplicate fields; private values omitted')
        result[key]=value
    db=result.get('DELIVERY_DATABASE_URL','')
    if not db.startswith(('postgresql://','postgres://')):
        raise ValueError('PostgreSQL settings required; no local storage fallback')
    url=urlsplit(result.get('DELIVERY_SUPABASE_URL',''))
    if url.scheme!='https' or not url.hostname or not url.hostname.endswith('.supabase.co') or url.path not in ('','/') or url.query or url.fragment or url.username or url.port:
        raise ValueError('fixed HTTPS Supabase project settings required')
    if not result.get('DELIVERY_SUPABASE_ANON_KEY'):
        raise ValueError('Supabase public key setting required')
    token=result.get('INTERVIEW_ENGINE_TOKEN','')
    if len(token)<24 or token=='local-interview-engine-token':
        raise ValueError('private engine token setting required; use existing owner settings setup')
    return result
