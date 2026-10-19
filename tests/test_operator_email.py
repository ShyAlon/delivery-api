"""The operator is the confirmed email in the signed login token, held in code. Nothing a user can edit grants admin."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from delivery_api import config
from delivery_api.supabase_auth import SupabaseIdentityVerifier
from delivery_api.tenant_http import TenantMiddleware

KEY = ec.generate_private_key(ec.SECP256R1())
URL = "https://test-project.supabase.co"
SUB = "00000000-0000-0000-0000-000000000001"


class Keys:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


def token(**changes):
    body = dict(sub=SUB, exp=int(time.time()) + 300, iat=int(time.time()), iss=URL + "/auth/v1", aud="authenticated", role="authenticated", session_id=SUB)
    body.update(changes)
    return jwt.encode(body, KEY, algorithm="ES256", headers={"kid": "test"})


def verifier():
    return SupabaseIdentityVerifier(URL, key_provider=Keys())


def test_the_operator_email_is_held_in_code():
    assert config.OPERATOR_EMAIL == "shy.alon@gmail.com"


@pytest.mark.parametrize("email", ["shy.alon@gmail.com", "Shy.Alon@Gmail.com", " shy.alon@gmail.com "])
def test_the_signed_operator_email_is_the_operator(email):
    scope, operator = verifier().verify_with_operator(token(email=email))
    assert operator is True and scope.owner_id == SUB


@pytest.mark.parametrize("claims", [
    dict(),                                                    # no email
    dict(email="someone@example.com"),
    dict(email="shy.alon@gmail.com.evil.invalid"),
    dict(email="xshy.alon@gmail.com"),
    dict(email=None),
    dict(email=["shy.alon@gmail.com"]),
    dict(email="shy.alon@gmail.com", user_metadata={"email_verified": False}),
    dict(user_metadata={"email": "shy.alon@gmail.com", "admin": True}),   # metadata is never read for admin
])
def test_nothing_else_is_the_operator(claims):
    assert verifier().verify_with_operator(token(**claims))[1] is False


def test_verify_still_returns_only_the_scope():
    assert verifier().verify(token(email="shy.alon@gmail.com")).owner_id == SUB


def _client(subjects=()):
    app = FastAPI()

    @app.get("/api/who")
    def who(request: Request):
        return {"admin": request.state.supabase_actor.admin}
    app.add_middleware(TenantMiddleware, verifier=verifier(), admin_subjects=subjects)
    return TestClient(app)


def _who(client, **claims):
    return client.get("/api/who", headers={"Authorization": "Bearer " + token(**claims)})


def test_the_request_actor_is_admin_for_the_operator_email_only():
    with _client() as client:
        assert _who(client, email="shy.alon@gmail.com").json() == {"admin": True}
        assert _who(client, email="other@example.com").json() == {"admin": False}
        assert _who(client).json() == {"admin": False}


def test_the_env_subject_list_still_grants_admin_and_a_forged_email_does_not():
    with _client(subjects=(SUB,)) as client:
        assert _who(client, email="other@example.com").json() == {"admin": True}
    with _client() as client:
        forged = jwt.encode(dict(sub=SUB, exp=int(time.time()) + 300, iat=int(time.time()), iss=URL + "/auth/v1", aud="authenticated", role="authenticated", session_id=SUB, email="shy.alon@gmail.com"),
                            ec.generate_private_key(ec.SECP256R1()), algorithm="ES256")
        assert client.get("/api/who", headers={"Authorization": "Bearer " + forged}).status_code == 401
