"""Strict Supabase Auth verification; caller supplies the fixed project URL.

No token-derived issuer/JWKS URL, no symmetric fallback, no metadata admin role.
The operator is the one fixed email (config.OPERATOR_EMAIL) in the signed claims, never a value from user_metadata.
JWKS caching is handled by PyJWT with bounded network timeout.
"""
from __future__ import annotations
from urllib.parse import urlsplit
from .tenancy import TenantScope, TenantAccessDenied


class SupabaseIdentityVerifier:
    def __init__(self, project_url: str, *, key_provider=None):
        import jwt
        parsed = urlsplit(project_url)
        if (parsed.scheme != 'https' or not parsed.hostname or
            not parsed.hostname.endswith('.supabase.co') or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or
            parsed.username or parsed.port):
            raise ValueError('fixed HTTPS Supabase project URL required')
        self.issuer = project_url.rstrip('/') + '/auth/v1'
        self.keys = key_provider or jwt.PyJWKClient(
            self.issuer + '/.well-known/jwks.json', timeout=5, lifespan=300)

    def verify(self, token: str) -> TenantScope:
        return self.verify_with_operator(token)[0]

    def verify_with_operator(self, token: str):
        """The verified scope, and whether the signed token is the operator's confirmed email. Same checks as verify; the email is read only from the signed claims."""
        import jwt
        from . import config
        if not token or len(token) > 16384:
            raise TenantAccessDenied('invalid access token')
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') not in ('ES256', 'RS256'):
                raise TenantAccessDenied('unsupported access-token algorithm')
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=['ES256','RS256'],
                audience='authenticated', issuer=self.issuer,
                options={'require':['sub','exp','iat','iss','aud','session_id'],
                         'verify_iat':True}, leeway=0)
            if claims.get('role') != 'authenticated':
                raise TenantAccessDenied('user access token required')
            email = claims.get('email')
            verified = (claims.get('user_metadata') or {}).get('email_verified') is not False
            operator = isinstance(email, str) and verified and email.strip().lower() == config.OPERATOR_EMAIL
            return TenantScope(claims['sub'],claims['session_id']), operator
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise TenantAccessDenied('invalid or expired access token') from exc
