"""Isolated no-provider process speaking the warm follow-up protocol."""
import array
import json
import os
import socket
import sys

mode=sys.argv[1]
channel=socket.socket(fileno=int(os.environ['REQUI_CHILD_FD']))
f=channel.makefile('rwb',buffering=0)
operation=json.loads(f.readline())
seq=0

def event(state,operation_id):
    global seq
    seq+=1
    f.write(json.dumps({'version':1,'operation_id':operation_id,'sequence':seq,'state':state}).encode()+b'\n')

event('ready',operation['operation_id']);event('running',operation['operation_id']);event('completed',operation['operation_id'])

if mode=='warm-trailing':
    # A forged trailing frame after a completed parked operation: the next
    # operation's identity/sequence check must reject it, never succeed.
    event('completed',operation['operation_id'])

# Follow-up frame: one sendmsg with descriptors.
fds=array.array('i')
data,ancillary,_flags,_addr=channel.recvmsg(65537,socket.CMSG_SPACE(16))
for level,ctype,cdata in ancillary:
    if level==socket.SOL_SOCKET and ctype==socket.SCM_RIGHTS:fds.frombytes(cdata[:len(cdata)//4*4])
while not data.endswith(b'\n'):data+=channel.recv(65537)
frame=json.loads(data)
# The gifted descriptor is live: answer one byte over it.
gift=socket.socket(fileno=fds[frame['fds']['broker']])
gift.sendall(b'k');gift.close()
event('running',frame['operation']['operation_id'])
event('completed',frame['operation']['operation_id'])

ending=channel.recv(6)
if mode in ('warm','warm-trailing') and ending==b'STOP\n':sys.exit(0)
if mode=='warm-eof' and ending==b'':sys.exit(0)
sys.exit(3)
