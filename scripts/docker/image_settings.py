"""Read existing owner settings as values, never execute or rewrite them."""
from pathlib import Path
import os
import re
import secrets
import tempfile
from urllib.parse import urlsplit


class SettingsProblem(ValueError):
    def __init__(self,check,fix):
        self.check=check;self.fix=fix
        super().__init__(check+': '+fix+'; private values omitted')


def read_values(path: Path) -> dict[str,str]:
    if path.is_symlink():raise SettingsProblem('file.symlink','use a private regular settings file, not a symlink')
    if not path.exists():raise SettingsProblem('file.exists','create this source file with hosted Auth values or select the correct --env-file')
    if not path.is_file():raise SettingsProblem('file.regular','select a regular file with --env-file')
    if path.stat().st_mode&0o077:raise SettingsProblem('file.private_mode','set mode 600 with chmod 600 on this file')
    if path.stat().st_size>262144:raise SettingsProblem('file.size','use a settings file smaller than256KiB')
    result={}
    for number,raw in enumerate(path.read_text().splitlines(),1):
        line=raw.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('export '):line=line[7:].lstrip()
        key,separator,value=line.partition('=')
        key=key.strip();value=value.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or key in result or '\0' in value:
            raise SettingsProblem('syntax.line_'+str(number),'fix malformed or duplicate assignment on this line')
        # Match the normal settings writer's quoted dotenv values without shell
        # evaluation, variable expansion, imports or external parser dependency.
        if value.startswith(("'",'"')):
            quote=value[0];end=None;escaped=False
            for index,char in enumerate(value[1:],1):
                if char==quote and not escaped:end=index;break
                if char=='\\' and not escaped:escaped=True
                else:escaped=False
            if end is None or value[end+1:].strip() and not value[end+1:].strip().startswith('#'):
                raise SettingsProblem('syntax.line_'+str(number),'close the quoted value and remove trailing non-comment text')
            value=value[1:end]
            value=value.replace('\\'+quote,quote).replace('\\\\','\\')
        else:
            value=re.split(r'\s+#',value,maxsplit=1)[0].rstrip()
        result[key]=value
    return result


def validate_auth(result):
    text=result.get('DELIVERY_SUPABASE_URL','')
    if not text:raise SettingsProblem('auth.project_url_present','supply DELIVERY_SUPABASE_URL in this source file')
    try:
        url=urlsplit(text)
        checks={'auth.https_scheme':url.scheme=='https','auth.project_host':bool(url.hostname and url.hostname.endswith('.supabase.co')),
          'auth.root_path':url.path in ('','/'),'auth.no_query_or_fragment':not(url.query or url.fragment),
          'auth.no_credentials':not(url.username or url.password),'auth.no_explicit_port':url.port is None}
    except ValueError:raise SettingsProblem('auth.project_url_parse','supply the project HTTPS origin, not a database URL') from None
    for check,ok in checks.items():
        if not ok:raise SettingsProblem(check,'supply DELIVERY_SUPABASE_URL as the HTTPS Supabase project origin without path, credentials, query or port')
    if not result.get('DELIVERY_SUPABASE_ANON_KEY'):raise SettingsProblem('auth.public_key_present','supply DELIVERY_SUPABASE_ANON_KEY from the project public settings')
    return result


def _persist_engine_token(path: Path, token: str) -> None:
    # Atomic append of one generated value; the file is already verified private.
    original=path.read_bytes()
    if original and not original.endswith(b'\n'):original+=b'\n'
    descriptor,temporary=tempfile.mkstemp(prefix='.engine-token-',dir=str(path.parent))
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(original+b'INTERVIEW_ENGINE_TOKEN='+token.encode('ascii')+b'\n')
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    except BaseException:
        try:os.unlink(temporary)
        except OSError:pass
        raise


def validate(path: Path, *, generate_missing_token: bool=False) -> dict[str,str]:
    result=read_values(path)
    db=result.get('DELIVERY_DATABASE_URL','')
    if not db.startswith(('postgresql://','postgres://')):
        raise ValueError('PostgreSQL settings required; no local storage fallback')
    validate_auth(result)
    if 'INTERVIEW_ENGINE_TOKEN' not in result:
        if not generate_missing_token:
            raise ValueError('private engine token setting missing; run req preview --apply: a private token is generated and saved to this file automatically. To choose your own, add INTERVIEW_ENGINE_TOKEN (24+ characters) instead')
        token=secrets.token_urlsafe(32)
        _persist_engine_token(path,token)
        result['INTERVIEW_ENGINE_TOKEN']=token
        return result
    token=result['INTERVIEW_ENGINE_TOKEN']
    if len(token)<24 or token=='local-interview-engine-token':
        raise ValueError('private engine token setting is empty, the known local placeholder, or under 24 characters: delete the INTERVIEW_ENGINE_TOKEN line from '+str(path)+' and rerun req preview --apply; a private token is generated and saved automatically (the value is never shown)')
    return result
