"""Disposable fake localhost preview. No production authentication or effects."""
import runpy,socket,threading,time,signal
from fastapi.responses import RedirectResponse,JSONResponse
from pathlib import Path
fixture=runpy.run_path(str(Path(__file__).with_name('ui-fixtures.py')))
service=fixture['service'];wrapped=fixture['wrapped'];create_app=fixture['create_app'];uvicorn=fixture['uvicorn']
# Outbound application connections are only the same-container API proxy.
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
def connect(self,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1'):raise PermissionError('Preview denies non-loopback egress')
 return original_connect(self,address)
def connect_ex(self,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1'):return 13
 return original_connect_ex(self,address)
socket.socket.connect=connect;socket.socket.connect_ex=connect_ex
@service.app.middleware('http')
async def preview_boundary(request,call_next):
 if request.method not in ('GET','HEAD'):return JSONResponse({'detail':'Fake preview is read-only'},status_code=405)
 response=await call_next(request)
 response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'"
 return response
backoffice=create_app(service_url='http://127.0.0.1:8000')
# This fake cookie is not a secret or a production login. Test script only.
for application in (service.app,backoffice):
 @application.get('/preview')
 def enter():
  response=RedirectResponse('/')
  response.set_cookie('requi_access',fixture['FAKE_TOKEN'],httponly=True,samesite='lax')
  return response
servers=[];threads=[];stop=threading.Event()
def shutdown(*_):stop.set()
signal.signal(signal.SIGINT,shutdown);signal.signal(signal.SIGTERM,shutdown)
try:
 for app,port in ((wrapped,8000),(backoffice,8020)):
  server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=port,lifespan='off',log_level='error'));servers.append(server)
  thread=threading.Thread(target=server.run);thread.start();threads.append(thread)
  for _ in range(200):
   if server.started:break
   time.sleep(.02)
  if not server.started:raise RuntimeError('Preview listener failed')
 print('FAKE PREVIEW READY: web8000 and back-office8020; GET-only; no provider/DB migration',flush=True)
 stop.wait()
finally:
 for server in servers:server.should_exit=True
 for thread in threads:thread.join(timeout=10)
