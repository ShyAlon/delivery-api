"""Strict Supabase Auth verification; caller supplies the fixed project URL.

No token-derived issuer/JWKS URL, no symmetric fallback, no metadata admin role.
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
        import jwt
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
            return TenantScope(claims['sub'],claims['session_id'])
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise TenantAccessDenied('invalid or expired access token') from exc
