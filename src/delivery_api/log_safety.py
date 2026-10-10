"""Bounded diagnostic projection. Never log raw credentials or payload bodies."""
import re
SENSITIVE=re.compile(r'(password|passwd|secret|token|authorization|cookie|api.?key|private.?key|dsn|database.?url|raw.?usage|prompt|payload|body)',re.I)
URL_USERINFO=re.compile(r'([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@',re.I)
ASSIGNMENT=re.compile(r'(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key)|authorization|cookie)\b\s*[:=]\s*[^\s,;]+')
BEARER=re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+')
QUERY=re.compile(r'(?i)([?&](?:token|key|secret|password|api_key|access_token)=)[^\s&#]+')


def safe_text(value,limit=1000):
    if not isinstance(value,(str,int,float,bool,type(None))):return '[structured value omitted]'
    value=str(value)
    if '-----BEGIN ' in value:return '[key material omitted]'
    value=URL_USERINFO.sub(r'\1[redacted]@',value)
    value=BEARER.sub('Bearer [redacted]',value)
    value=QUERY.sub(r'\1[redacted]',value)
    value=ASSIGNMENT.sub(lambda m:m.group(1)+'=[redacted]',value)
    value=''.join(c if ord(c)>=32 and ord(c)!=127 else ' ' for c in value)
    return value[:limit]+(' [truncated]' if len(value)>limit else '')


def safe_fields(fields):
    result={}
    for key,value in list(fields.items())[:30]:
        name=safe_text(key,80)
        result[name]='[redacted]' if SENSITIVE.search(name) else safe_text(value,500)
    return result
