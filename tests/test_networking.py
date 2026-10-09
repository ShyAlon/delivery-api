

def test_console_entrypoint_uses_same_explicit_listener(monkeypatch):
 import uvicorn
 from delivery_api.__main__ import main
 calls=[];monkeypatch.setenv('DELIVERY_HOST','127.0.0.1');monkeypatch.setenv('DELIVERY_PORT','9876');monkeypatch.delenv('PORT',raising=False)
 monkeypatch.setattr(uvicorn,'run',lambda *args,**kwargs:calls.append((args,kwargs)))
 main();assert calls==[(('delivery_api.app:app',),{'host':'127.0.0.1','port':9876,'reload':False})]
