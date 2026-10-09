"""ASGI tenant boundary, including scope lifetime through streaming responses."""
from starlette.responses import JSONResponse
from .tenancy import tenant_context, TenantAccessDenied


class TenantMiddleware:
    def __init__(self,app,*,verifier,admin_subjects=(),session_check=None):
        self.app=app
        self.session_check=session_check
        self.verifier=verifier
        self.admin_subjects=frozenset(admin_subjects)

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        path=scope.get('path','')
        # Static/login shell may load without identity. Every data API and old
        # link/run endpoint needs identity. Legacy auth endpoints cannot mint
        # local sessions in cloud mode.
        # New endpoints are private by default. Only known shell/static routes
        # may bypass identity, never every path outside an /api prefix.
        import re
        public=(path in ('/','/about','/favicon.ico','/health','/api/auth','/sessions')
                or path.startswith('/assets/')
                or re.fullmatch(r'/tasks/[^/]+/(?:view|complete|gates/[^/]+|decisions/[^/]+)',path) is not None
                or re.fullmatch(r'/sessions/[^/]+/(?:view|[^/]+)',path) is not None)
        if path.startswith('/api/auth/') or (path.startswith('/auth/') and path not in ('/auth/signin','/auth/signup','/auth/refresh','/auth/logout')):
            return await JSONResponse({'detail':'Use Supabase Auth in cloud mode'},status_code=404)(scope,receive,send)
        # Scoped engine protocol/recovery is not available yet. Prevent cloud
        # calls from entering legacy shared-worker/session paths.
        blocked_suffixes=('/start','/simulate',
                          '/advance','/answer','/respond','/route','/model-route','/decision','/resolve')
        if scope.get('method') not in ('GET','HEAD','OPTIONS') and (
            path.startswith(('/runs','/links')) or
            path.endswith(blocked_suffixes) or ('/gates/' in path and not path.endswith(('/decide','/revision-drafts'))) or
            '/simulations' in path or '/engine/' in path):
            return await JSONResponse({'detail':'Cloud execution pending scoped worker protocol'},status_code=503)(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        raw=headers.get(b'authorization',b'').decode('latin1')
        from http.cookies import SimpleCookie
        cookies=SimpleCookie()
        try:cookies.load(headers.get(b'cookie',b'').decode('latin1'))
        except Exception:pass
        cookie=cookies.get('requi_access')
        if not raw and cookie:raw='Bearer '+cookie.value
        if path in ('/auth/signin','/auth/signup','/auth/refresh','/auth/logout'):
            return await self.app(scope,receive,send)
        if not raw:
            if public:return await self.app(scope,receive,send)
            return await JSONResponse({'detail':'Supabase sign-in required'},status_code=401)(scope,receive,send)
        if not raw.startswith('Bearer ') or len(raw.split())!=2:
            return await JSONResponse({'detail':'Invalid authorization'},status_code=401)(scope,receive,send)
        try:
            from starlette.concurrency import run_in_threadpool
            identity=await run_in_threadpool(self.verifier.verify,raw[7:])
        except TenantAccessDenied:
            if path=='/api/auth':return await self.app(scope,receive,send)
            return await JSONResponse({'detail':'Invalid or expired access token'},status_code=401)(scope,receive,send)
        from .auth import Actor
        scope.setdefault('state',{})['supabase_actor']=Actor(
            identity.owner_id,'Signed-In User',admin=identity.owner_id in self.admin_subjects)
        try:
            with tenant_context(identity):
                if self.session_check:
                    active=await run_in_threadpool(self.session_check)
                    if not active:
                        scope['state'].pop('supabase_actor',None)
                        if path=='/api/auth':return await self.app(scope,receive,send)
                        return await JSONResponse({'detail':'Session expired or revoked'},status_code=401)(scope,receive,send)
                return await self.app(scope,receive,send)
        except TenantAccessDenied:
            return await JSONResponse({'detail':'Tenant resource not found'},status_code=404)(scope,receive,send)
