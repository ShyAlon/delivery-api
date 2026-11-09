"""Warm follow-up framing: one sendmsg carries JSON + private descriptors."""
import array
import json
import os
import socket

from delivery_api.child_entry import recv_frame


def pair():
    return socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)


def send_frame(sock,payload,fds):
    rights=array.array('i',fds)
    sock.sendmsg([json.dumps(payload,separators=(',',':')).encode()+b'\n'],
        [(socket.SOL_SOCKET,socket.SCM_RIGHTS,rights)])


def test_frame_round_trips_json_and_descriptors():
    parent,child=pair()
    gift=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    payload={'version':1,'binding':{'job_id':'job-9'},'operation':{'kind':'interview.answer'},
        'input':{'text':'yes'},'input_ref':'answer-1','fds':{'broker':0,'compute':1}}
    send_frame(parent,payload,[gift[0].fileno(),gift[0].fileno()])
    received,fds=recv_frame(child)
    assert received==payload and len(fds)==2
    # The received descriptor is a live duplicate of the gift, not the number.
    assert fds[0]!=gift[0].fileno() or os.fstat(fds[0]).st_ino==os.fstat(gift[0].fileno()).st_ino
    probe=socket.socket(fileno=fds[0]);probe.settimeout(2)
    gift[1].sendall(b'ping')
    assert probe.recv(4)==b'ping'


def test_stop_sentinel_and_eof():
    parent,child=pair()
    parent.sendmsg([b'STOP\n'])
    assert recv_frame(child)==('STOP',[])
    parent.close()
    assert recv_frame(child)==(None,[])


def test_frame_larger_than_one_read_round_trips():
    parent,child=pair()
    gift=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    payload={'version':1,'binding':{},'operation':{'kind':'interview.answer'},'blob':'x'*40000,
        'fds':{'broker':0}}
    send_frame(parent,payload,[gift[0].fileno()])
    received,fds=recv_frame(child)
    assert received==payload and len(fds)==1
