"""Owned system PostgreSQL for architecture-native image smoke, Unix socket only."""
import os
from pathlib import Path
import shutil
import subprocess

class Server:
    def __init__(self,scratch,cleanup_mode='delete'):
        self.scratch=Path(scratch);self.bin=Path(os.environ['REQUI_SMOKE_PG_BIN'])
        self.ctl=self.bin/'pg_ctl'
        self._run('initdb','-D',str(self.scratch),'-U','postgres','--auth=trust','--encoding=UTF8','--no-locale')
        self._run('pg_ctl','-D',str(self.scratch),'-l',str(self.scratch/'server.log'),'-o','-F -h "" -k '+str(self.scratch),'-w','start')
    def _run(self,command,*args):
        r=subprocess.run([str(self.bin/command),*args],capture_output=True,text=True,timeout=30)
        if r.returncode:raise RuntimeError('disposable PostgreSQL '+command+' failed (no caller DSN accepted)')
    def get_uri(self):return 'postgresql://postgres@/postgres?'+'host='+str(self.scratch)
    def cleanup(self):
        subprocess.run([str(self.ctl),'-D',str(self.scratch),'-m','fast','-w','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        shutil.rmtree(self.scratch,ignore_errors=True)
