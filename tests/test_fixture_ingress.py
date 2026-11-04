"""Real native fixed-destination TCP relay, not a Docker networking claim."""
import importlib.util
from pathlib import Path
import socket
import threading
import pytest
D=Path(__file__).resolve().parents[1]/'scripts/docker'
spec=importlib.util.spec_from_file_location('fixture_ingress',D/'fixture_ingress.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_relay_roundtrip_does_not_interpret_connect_or_host_header():
    with socket.socket() as target:
        target.bind(('127.0.0.1',0));target.listen()
        a,b=socket.socketpair()
        def echo():
            with target.accept()[0] as connection:connection.sendall(connection.recv(4096))
        worker=threading.Thread(target=echo);worker.start()
        relay=threading.Thread(target=m.relay,args=(b,target.getsockname()));relay.start()
        try:
            raw=b'CONNECT external.invalid:443 HTTP/1.1\r\nHost: external.invalid\r\n\r\n'
            a.settimeout(3);a.sendall(raw);assert a.recv(4096)==raw
        finally:a.close();worker.join(3);relay.join(3);b.close()
        assert not relay.is_alive() and not worker.is_alive()


def test_relay_destination_refuses_external_or_arbitrary_hosts():
    for value in ('example.com','127.0.0.1','requi-fixture-service-bad'):
        with pytest.raises(ValueError):m.serve(value)


def test_service_is_internal_only_and_only_owned_relay_has_gateway():
    text=(D/'local_image_run.py').read_text()
    assert 'self.volume,publish=False' in text
    assert "'--network',self.front,'--publish',target.binding" in text
    assert "['docker','network','connect',self.network,self.ingress]" in text
    assert "['docker','network','connect',self.front,self.service]" not in text
