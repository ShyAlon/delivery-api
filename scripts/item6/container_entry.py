#!/usr/bin/env python3
"""Disposable in-container PostgreSQL, no host ports or production data volumes."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from bootstrap_database import bootstrap


def main():
 os.umask(0o077)
 p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--run-id',required=True);p.add_argument('--go')
 a=p.parse_args();cmd=[sys.executable,'/app/item6/mac_run.py','--config',str(a.config),'--run-id',a.run_id]
 # Dry preflight is always first; nothing initializes unless exact go passes.
 subprocess.run(cmd,check=True)
 if a.go is None:return
 if a.go!=a.run_id:raise ValueError('exact go required')
 config=json.loads(a.config.read_text());data=Path('/tmp/item6-pg')
 if data.exists():raise ValueError('new container database required; do not retry a partial run')
 subprocess.run(['/usr/lib/postgresql/15/bin/initdb','-D',str(data),'--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'-l','/tmp/item6-pg-server.log','-o','-h 127.0.0.1 -p 55439 -k /tmp','start'],check=True,stdout=subprocess.DEVNULL)
 try:
  subprocess.run(['/usr/lib/postgresql/15/bin/createdb','-h','127.0.0.1','-p','55439','--template=template0',config['database_name']],check=True)
  dsn='host=127.0.0.1 port=55439 user=requi dbname='+config['database_name']
  result=bootstrap(dsn,'/app/migrations')
  print(json.dumps(result),flush=True)
  subprocess.run(cmd+['--go',a.go],check=True)
 finally:
  # Preserve the entire DB for private review, including partial failures. Do
  # not truncate/reset it. Archive before container exit, with local-only rail.
  root=Path(config['root']);archive=root.parent/'item6-database.dump'
  if archive.exists():raise ValueError('existing DB archive must not be overwritten')
  fd=os.open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'wb') as out:
   subprocess.run(['/usr/lib/postgresql/15/bin/pg_dump','-h','127.0.0.1','-p','55439','-Fc',config['database_name']],stdout=out,check=True)
  subprocess.run(['/usr/lib/postgresql/15/bin/pg_ctl','-D',str(data),'stop','-m','fast'],check=True,stdout=subprocess.DEVNULL)

if __name__=='__main__':
 try:main()
 except Exception:
  print('Item6 container stopped; preserve evidence and do not repeat the run.',file=sys.stderr);raise SystemExit(1) from None
