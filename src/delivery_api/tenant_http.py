"""ASGI tenant boundary, including scope lifetime through streaming responses."""
from starlette.responses import JSONResponse
from .tenancy import tenant_context, TenantAccessDenied


class TenantMiddleware:
    def __init__(self,app,*,verifier,admin_subjects=(),session_check=None,request_database=None):
        self.app=app
        self.request_database=request_database
        self.session_check=session_check
        self.verifier=verifier
        self.admin_subjects=frozenset(admin_subjects)

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        path=scope.get('path','')
        # Public static files and the health probe carry no tenant data. They are never authenticated: a signed-in browser sends its cookie with
        # every file, and verifying it plus the session database check for each of ~30 files per page load made pages slow and let the burst fail.
        if path.startswith('/assets/') or path in ('/favicon.ico','/health'):
            return await self.app(scope,receive,send)
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
            '/simulations' in path or '/engine/' in path) and not _interview_route_open(path):
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
            if hasattr(self.verifier,'verify_with_operator'):identity,operator=await run_in_threadpool(self.verifier.verify_with_operator,raw[7:])
            else:identity,operator=await run_in_threadpool(self.verifier.verify,raw[7:]),False
        except TenantAccessDenied:
            if path=='/api/auth':return await self.app(scope,receive,send)
            return await JSONResponse({'detail':'Invalid or expired access token'},status_code=401)(scope,receive,send)
        from .auth import Actor
        scope.setdefault('state',{})['supabase_actor']=Actor(
            identity.owner_id,'Signed-In User',admin=operator or identity.owner_id in self.admin_subjects)
        shared=None
        if self.request_database is not None and _shares_one_transaction(scope.get('method'),path):
            shared=self.request_database.begin_request(identity)
        failed=True
        try:
            await self._serve(scope,receive,send,path,identity)
            failed=False
        finally:
            if shared is not None:
                from starlette.concurrency import run_in_threadpool
                self.request_database.end_context(shared[1])
                await run_in_threadpool(self.request_database.finish_request,shared[0],failed)

    async def _serve(self,scope,receive,send,path,identity):
        from starlette.concurrency import run_in_threadpool
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


_INTERVIEW_ROUTES=('start','answer')


def _interview_route_open(path):
    """The only blocked routes reopened so far: starting an interview and answering it. Open only while the in-process engine is installed,
    which happens only when execution is OPEN with every startup check passing; the money gateway still admits each model call."""
    import re
    m=re.fullmatch(r'/api/tasks/[^/]+/([a-z]+)',path)
    if m is None or m.group(1) not in _INTERVIEW_ROUTES:return False
    from . import cloud_runtime
    return cloud_runtime.installed()


def _shares_one_transaction(method,path):
    """Plain reads answered from the database share one transaction. Streams and file downloads stay out so they never hold a connection."""
    return (method in ('GET','HEAD') and path.startswith('/api/') and not path.endswith(('/stream','.zip')) and '/artifacts/' not in path)
