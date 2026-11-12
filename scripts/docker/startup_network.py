"""Bounded TCP-only startup check. Passwords are stdin, never argv or output.

The short-lived subprocess bounds DNS and all resolved-address attempts too.
A host result is host evidence, not proof about Docker's network namespace.
"""
import json
import re
import socket
import subprocess
import sys
import time

TCP_BUDGET_SECONDS=4


def endpoint(dsn):
    from psycopg.conninfo import conninfo_to_dict
    fields=conninfo_to_dict(dsn)
    hosts=fields.get('host','').split(',');ports=fields.get('port','5432').split(',')
    # libpq multi-host, service files and local sockets keep their own semantics.
    if len(hosts)!=1 or len(ports)!=1 or not hosts[0] or hosts[0].startswith('/') or fields.get('service'):
        return None
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,253}',hosts[0]):return None
    return hosts[0],int(ports[0])


def probe(dsn,*,run=None):
    run=run or subprocess.run
    try:
        result=run([sys.executable,__file__,'--probe'],input=dsn,capture_output=True,text=True,timeout=TCP_BUDGET_SECONDS+1)
        value=json.loads(result.stdout)
        if value.get('status') not in ('reachable','unreachable','unknown','local-or-multihost'):raise ValueError('status')
        return value
    except subprocess.TimeoutExpired:
        return {'status':'unreachable','reason':'timeout','scope':'host TCP (DNS and all addresses bounded)'}
    except Exception:return {'status':'unknown','reason':'probe unavailable','scope':'host TCP'}


def main():
    dsn=sys.stdin.read(16384)
    try:target=endpoint(dsn)
    except Exception:
        print(json.dumps({'status':'unknown','reason':'endpoint unavailable','scope':'host TCP'}));return
    if target is None:
        print(json.dumps({'status':'local-or-multihost','reason':'TCP precheck not applicable','scope':'host TCP'}));return
    host,port=target;began=time.monotonic()
    result={'status':'unreachable','reason':'connection failed','scope':'host TCP','host':host,'port':port}
    try:
        # Parent kills this process at the wall-clock deadline if resolution
        # itself hangs. The remaining budget covers all resolved addresses.
        addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
        for family,kind,proto,_,address in addresses:
            remaining=TCP_BUDGET_SECONDS-(time.monotonic()-began)
            if remaining<=0:raise TimeoutError()
            try:
                with socket.socket(family,kind,proto) as connection:
                    connection.settimeout(remaining);connection.connect(address)
                result.update(status='reachable',reason='TCP accepted');break
            except TimeoutError:result['reason']='timeout'
            except OSError:pass
    except TimeoutError:result['reason']='timeout'
    except OSError:result['reason']='DNS or connection failure'
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
