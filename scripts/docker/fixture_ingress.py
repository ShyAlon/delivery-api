"""Test-only TCP ingress. One fixed destination, no HTTP proxy/CONNECT routing."""
import re
import select
import socket
import socketserver
import sys


def relay(client,target):
    with socket.create_connection(target,timeout=2) as upstream:
        client.settimeout(5);upstream.settimeout(5)
        peers={client:upstream,upstream:client}
        while True:
            ready,_,_=select.select(list(peers),[],[],30)
            if not ready:return
            for source in ready:
                data=source.recv(65536)
                if not data:return
                peers[source].sendall(data)


def serve(host):
    if not re.fullmatch(r'requi-fixture-service-[a-f0-9]{24}',host):
        raise ValueError('fixed owned service destination required')
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:relay(self.request,(host,8000))
            except OSError:pass
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True
        daemon_threads=True
    with Server(('0.0.0.0',8000),Handler) as server:server.serve_forever()


if __name__=='__main__':serve(sys.argv[1])
