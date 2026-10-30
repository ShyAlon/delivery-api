"""ASGI tenant boundary, including scope lifetime through streaming responses."""
from starlette.responses import JSONResponse, RedirectResponse
from .tenancy import tenant_context, TenantAccessDenied


# Pages a browser can open that need a signed-in person. Without one the browser is sent to the sign-in page, not shown a JSON error.
SIGN_IN_PAGES=('/ops','/credits')


def wants_html(scope):
    """True for a browser opening a page (a GET or HEAD that accepts HTML). Scripts and fetch calls do not, so they keep the JSON answers."""
    if scope.get('method') not in ('GET','HEAD'):return False
    return b'text/html' in dict(scope.get('headers',[])).get(b'accept',b'').lower()


def sign_in_redirect(path):
    """Where a signed-out browser goes: the sign-in page, which returns the person to this page afterwards. Only the fixed pages above come back."""
    return RedirectResponse('/?next='+path,status_code=302,headers={'Cache-Control':'no-store'})


SESSION_CHECK_TTL_SECONDS=30.0

class SessionCheckCache:
    """The session-liveness check is one remote round trip per request. Cached per
    verified owner/session pair for a short TTL: sign-out or revocation takes effect up to
    SESSION_CHECK_TTL_SECONDS late (approved tradeoff, 2026-10-27). Failures to
    reach the database raise and are never cached; a False (expired/revoked) is
    cached too - a fresh sign-in is a new session id, never blocked by it."""
    def __init__(self,check,ttl=SESSION_CHECK_TTL_SECONDS):
        import threading,time
        self._check=check;self._ttl=ttl;self._time=time.monotonic;self._lock=threading.Lock();self._entries={};self._inflight={}
    def active(self,owner_id,session_id):
        key=(owner_id,session_id)
        now=self._time()
        from concurrent.futures import Future
        with self._lock:
            hit=self._entries.get(key)
            if hit is not None and hit[0]>now:return hit[1]
            pending=self._inflight.get(key)
            leader=pending is None
            if leader:
                pending=Future();self._inflight[key]=pending
        if not leader:return pending.result()
        try:
            active=self._check()
            with self._lock:
                if len(self._entries)>=1000:self._entries={key:entry for key,entry in self._entries.items() if entry[0]>now}
                # TTL remains anchored to check START, never extended by waiting.
                self._entries[key]=(now+self._ttl,active)
            pending.set_result(active)
            return active
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._lock:self._inflight.pop(key,None)



class TenantMiddleware:
    def __init__(self,app,*,verifier,admin_subjects=(),session_check=None,request_database=None):
        self.app=app
        self.request_database=request_database
        self.session_check=SessionCheckCache(session_check) if session_check is not None else None
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
                or (path=='/api/client-log' and scope.get('method')=='POST')
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
            path.endswith(blocked_suffixes) or ('/gates/' in path and not path.endswith(('/decide','/revision-drafts','/stack-decisions'))) or
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
        browser=wants_html(scope)
        if not raw:
            if public:return await self.app(scope,receive,send)
            if browser and path in SIGN_IN_PAGES:return await sign_in_redirect(path)(scope,receive,send)
            return await JSONResponse({'detail':'Supabase sign-in required'},status_code=401)(scope,receive,send)
        if not raw.startswith('Bearer ') or len(raw.split())!=2:
            if browser and public:return await self.app(scope,receive,send)     # a page with a damaged cookie opens as signed out, so the sign-in screen can show
            if browser and path in SIGN_IN_PAGES:return await sign_in_redirect(path)(scope,receive,send)
            return await JSONResponse({'detail':'Invalid authorization'},status_code=401)(scope,receive,send)
        try:
            from starlette.concurrency import run_in_threadpool
            email=None
            import time as _time
            from .postgres_scope import note_auth
            _t_auth=_time.monotonic()
            try:
                if hasattr(self.verifier,'verify_with_email'):identity,operator,email=await run_in_threadpool(self.verifier.verify_with_email,raw[7:])
                elif hasattr(self.verifier,'verify_with_operator'):identity,operator=await run_in_threadpool(self.verifier.verify_with_operator,raw[7:])
                else:identity,operator=await run_in_threadpool(self.verifier.verify,raw[7:]),False
            finally:note_auth(_time.monotonic()-_t_auth)
        except TenantAccessDenied:
            if path=='/api/auth':return await self.app(scope,receive,send)
            if browser and public:return await self.app(scope,receive,send)     # an expired token on a page: open it signed out, the page signs the person in again
            if browser and path in SIGN_IN_PAGES:return await sign_in_redirect(path)(scope,receive,send)
            return await JSONResponse({'detail':'Invalid or expired access token'},status_code=401)(scope,receive,send)
        from .auth import Actor
        scope.setdefault('state',{})['supabase_actor']=Actor(
            identity.owner_id,'Signed-In User',email,admin=operator or identity.owner_id in self.admin_subjects)
        shared=None
        if self.request_database is not None and _shares_one_transaction(scope.get('method'),path):
            shared=self.request_database.begin_request(identity)
        failed=True
        try:
            await self._serve(scope,receive,send,path,identity,browser,public)
            failed=False
        finally:
            if shared is not None:
                from starlette.concurrency import run_in_threadpool
                self.request_database.end_context(shared[1])
                await run_in_threadpool(self.request_database.finish_request,shared[0],failed)

    async def _serve(self,scope,receive,send,path,identity,browser=False,public=False):
        from starlette.concurrency import run_in_threadpool
        try:
            with tenant_context(identity):
                if self.session_check:
                    import time as _time
                    from .postgres_scope import note_auth
                    _t_check=_time.monotonic()
                    try:active=await run_in_threadpool(self.session_check.active,identity.owner_id,identity.session_id)
                    finally:note_auth(_time.monotonic()-_t_check)
                    if not active:
                        scope['state'].pop('supabase_actor',None)
                        if path=='/api/auth':return await self.app(scope,receive,send)
                        if browser and public:return await self.app(scope,receive,send)
                        if browser and path in SIGN_IN_PAGES:return await sign_in_redirect(path)(scope,receive,send)
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
