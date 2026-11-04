"""Disposable external Auth/JWKS and zero-cost Ollama protocol fixtures.

Not imported by the service image. No identity-verifier or SDK monkeypatches.
The Docker harness gives the TLS listener an isolated fictional Supabase alias.
"""
from __future__ import annotations
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OWNER = "00000000-0000-0000-0000-000000000001"
SESSION = "00000000-0000-0000-0000-0000000000a1"
MODEL = "phi4"
AUTH_HOST = "image-fixture.supabase.co"
ISSUER = "https://" + AUTH_HOST + "/auth/v1"


def keys(directory: Path) -> dict:
    """Ephemeral test CA/server/JWT keys. Never account credentials."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import jwt
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    signing_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable image fixture CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, AUTH_HOST)]))
            .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(AUTH_HOST)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256()))
    (directory/"ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory/"server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory/"server-key.pem").write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for path in directory.iterdir():path.chmod(0o600)
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update(kid="image-fixture", alg="ES256", use="sig")
    token = jwt.encode(dict(sub=OWNER, session_id=SESSION, exp=int(time.time())+3600,
                           iat=int(time.time()), iss=ISSUER, aud="authenticated",
                           role="authenticated", email="owner-a@example.test"), signing_key,
                       algorithm="ES256", headers={"kid":"image-fixture"})
    return {"jwks":{"keys":[jwk]}, "token":token}


def provider_response(payload: dict) -> dict:
    content = ({"is_web_app":True, "archetype":"saas_crud", "confidence":0.9, "reason":"fictional fixture app"}
               if "is_web_app" in json.dumps(payload) else
               {"question":"What does a booking look like for your customers?", "covered":[], "thin":[], "skipped":[], "complete":False})
    message = {"role":"assistant", "content":json.dumps(content)}
    finish = "stop"
    if payload.get("tools"):
        name = payload["tools"][0]["function"]["name"]
        message["tool_calls"] = [{"id":"fixture-call", "type":"function", "function":{"name":name, "arguments":json.dumps({"payload":content})}}]
        finish = "tool_calls"
    return {"id":"fixture", "object":"chat.completion", "model":payload["model"],
            "choices":[{"index":0,"message":message,"finish_reason":finish}],
            "usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}


class Services:
    def __init__(self, directory: Path):
        self.material = keys(directory)
        self.calls = []
        self.active = threading.Event()
        self.release = threading.Event()
        active, release = self.active, self.release
        material, calls = self.material, self.calls
        class Handler(BaseHTTPRequestHandler):
            def send(self, status, body):
                raw=json.dumps(body).encode()
                self.send_response(status);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                if self.path=="/auth/v1/.well-known/jwks.json":return self.send(200,material["jwks"])
                if self.path=="/v1/models":return self.send(200,{"data":[{"id":MODEL}]})
                if self.path=="/health":return self.send(200,{"ok":True,"fixture":True,"calls":len(calls),"active":active.is_set()})
                return self.send(404,{"error":"fixture path absent"})
            def do_POST(self):
                if self.path=='/fixture/release':
                    release.set();return self.send(200,{'released':True})
                if self.path!="/v1/chat/completions":return self.send(404,{"error":"fixture path absent"})
                size=int(self.headers.get("content-length","0"))
                if not 0<size<=2_000_000:return self.send(413,{"error":"fixture request bound"})
                payload=json.loads(self.rfile.read(size))
                if payload.get("model")!=MODEL:return self.send(400,{"error":"fixture model mismatch"})
                calls.append({"model":MODEL,"call":len(calls)+1})
                if "EXACT_IMAGE_STOP" in json.dumps(payload):
                    active.set()
                    try:release.wait(60)
                    finally:active.clear()
                # A deterministic pause makes Stop acceptance exercise an active call.
                if payload.get("stream") is True:return self.send(400,{"error":"fixture requires nonstreamed wire"})
                return self.send(200,provider_response(payload))
            def log_message(self,*args):pass
        self.handler = Handler
        self.servers=[]

    def start(self, *, host="127.0.0.1", auth_port=0, provider_port=0, directory: Path):
        auth=ThreadingHTTPServer((host,auth_port),self.handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory/"server.pem", directory/"server-key.pem")
        auth.socket=context.wrap_socket(auth.socket, server_side=True)
        provider=ThreadingHTTPServer((host,provider_port),self.handler)
        self.servers=[auth,provider]
        for server in self.servers:
            threading.Thread(target=server.serve_forever,daemon=True).start()
        return auth.server_port, provider.server_port

    def close(self):
        for server in self.servers:server.shutdown();server.server_close()
