"""Isolated no-provider process to exercise the supervised descriptor seam."""
import json
import os
import signal
import socket
import sys
import time

mode=sys.argv[1]
channel=socket.socket(fileno=int(os.environ['REQUI_CHILD_FD']))
f=channel.makefile('rwb',buffering=0)
operation=json.loads(f.readline())
assert 'DEEPSEEK_API_KEY' not in os.environ and 'DELIVERY_DATABASE_URL' not in os.environ
seq=0

def event(state):
    global seq
    seq+=1
    body={'version':1,'operation_id':operation['operation_id'],'sequence':seq,'state':state}
    if mode=='foreign':body['operation_id']='foreign'
    f.write(json.dumps(body).encode()+b'\n')

if mode=='oversize':f.write(b'x'*9000+b'\n');sys.exit(0)
if mode=='bad-frame':f.write(b'not-json\n');sys.exit(0)
event('ready');event('running')
if mode=='descendant':
    import subprocess
    kid=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],close_fds=True)
    open(sys.argv[2],'w').write(str(kid.pid))
if mode=='crash':os._exit(17)
if mode=='stuck':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    while True:time.sleep(.02)
if mode=='cooperative':
    assert f.readline()==b'STOP\n';event('stopped')
else:event('completed')
if mode=='duplicate':event('completed')
f.close();channel.close()
