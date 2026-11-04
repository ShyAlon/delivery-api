#!/usr/bin/env python3
"""Owned local fixture service for unchanged-image acceptance, never production.

Runs only inside the test harness container. No caller DSN is accepted. The
shared volume contains a Unix socket and public CA, not PostgreSQL data or keys.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time


def main():
    forbidden=('DELIVERY_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL',
               'DEEPSEEK_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY')
    if any(os.environ.get(key) for key in forbidden):
        raise RuntimeError('fixture refuses inherited database/provider settings')
    root=Path('/harness')
    sys.path.insert(0,str(root/'tests'))
    import pg_harness
    import importlib.util
    spec=importlib.util.spec_from_file_location('system_pg',root/'scripts/docker/system-pg-smoke.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    socket_dir=Path('/fixture/socket');socket_dir.mkdir(mode=0o700,parents=True)
    class Server(module.Server):
        def __init__(self,scratch,cleanup_mode='delete'):
            self.scratch=Path(scratch);self.bin=Path(os.environ['REQUI_SMOKE_PG_BIN']);self.ctl=self.bin/'pg_ctl'
            self._run('initdb','-D',str(self.scratch),'-U','postgres','--auth=trust','--encoding=UTF8','--no-locale')
            self._run('pg_ctl','-D',str(self.scratch),'-l',str(self.scratch/'server.log'),'-o','-F -h "" -k '+str(socket_dir),'-w','start')
        def get_uri(self):return 'postgresql://postgres@/postgres?host='+str(socket_dir)
    pg_harness.start(server_factory=Server)
    if pg_harness.STATE['status']!='running':raise RuntimeError('owned fixture PG initialization failed')
    import psycopg
    from fixture_services import Services, MODEL, ISSUER, OWNER
    from psycopg.conninfo import conninfo_to_dict
    from urllib.parse import quote,urlencode
    fields=conninfo_to_dict(pg_harness.STATE['dsn']);database=fields.pop('dbname');fields['user']='delivery_app'
    dsn='postgresql:///'+quote(database,safe='')+'?'+urlencode(fields)
    row={'provider':'ollama','model':MODEL,'price_version':'image-fixture-v1','source':'owned zero-cost fixture',
         'expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9}
    ceiling={'model':MODEL,'context_tokens':32768,'maximum_output_tokens':12288,
             'source':'owned zero-cost fixture','bound_version':'image-fixture-v1','protective_run_ceiling':False}
    payload={'row':row,'context':ceiling}
    import hashlib
    revision=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with psycopg.connect(pg_harness.STATE['dsn']) as connection:
        connection.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000000000,0)',(OWNER,))
        connection.execute('INSERT INTO admission_limits VALUES(%s,%s,86400,NULL,NULL,NULL,NULL,60,NULL,3)',(OWNER,'image-fixture'))
        connection.execute("INSERT INTO model_pin_revisions(revision,provider,model,payload,fetched_at,operator_subject,reason) VALUES(%s,'ollama',%s,%s::jsonb,now(),%s,'owned image fixture')",(revision,MODEL,json.dumps(payload),OWNER))
        connection.execute("INSERT INTO model_pin_heads(provider,model,revision,is_default) VALUES('ollama',%s,%s,true)",(MODEL,revision))
    private=Path('/tmp/fixture-keys')
    services=Services(private)
    # CA is public. Keep TLS/signing material inside the fixture container.
    (Path('/fixture')/'ca.pem').write_bytes((private/'ca.pem').read_bytes())
    (Path('/fixture')/'ca.pem').chmod(0o644)
    services.start(host='0.0.0.0',auth_port=443,provider_port=11434,directory=private)
    # Handoff consumed via a private docker exec read, never logged or stored in
    # an acceptance receipt. This token grants fictional disposable identity only.
    state=Path('/tmp/fixture-state.json')
    state.write_text(json.dumps({'dsn':dsn,'token':services.material['token'],'issuer':ISSUER}))
    state.chmod(0o600)
    print('fixture ready: owned PG, signed Auth/JWKS and zero-cost provider',flush=True)
    stop=False
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    try:
        while not stop:time.sleep(.2)
    finally:services.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
