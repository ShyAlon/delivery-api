#!/usr/bin/env python3
"""Passive sandbox webhook evidence spool. Never verifies, posts credits or logs payloads."""
import argparse,base64,hashlib,http.server,json,os,re,stat,sys,threading,time,uuid
from pathlib import Path

HEADERS=('paypal-auth-algo','paypal-cert-url','paypal-transmission-id','paypal-transmission-sig','paypal-transmission-time')
PATH='/sandbox/paypal/webhook'
MAX_BODY=262144
MAX_FILES=256
MAX_DISK=128*1024*1024
LOCK=threading.Lock()

class EvidenceError(ValueError):pass

def private_dir(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_dir() or stat.S_IMODE(path.stat().st_mode)&0o077:
        raise EvidenceError('private non-symlink evidence directory required')
    return path

def pairs(items):
    out={}
    for k,v in items:
        if k in out:raise EvidenceError('duplicate JSON field rejected')
        out[k]=v
    return out

def parse_raw(raw):
    try:body=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('nonfinite JSON rejected')))
    except (ValueError,UnicodeError):raise EvidenceError('bounded strict event JSON required') from None
    if not isinstance(body,dict) or not isinstance(body.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,127}',body['id']):
        raise EvidenceError('bounded event identity required')
    if body.get('event_type')!='PAYMENT.CAPTURE.COMPLETED' or not isinstance(body.get('resource'),dict):
        raise EvidenceError('PAYMENT.CAPTURE.COMPLETED resource required')
    return body

def original_headers(items):
    out={}
    for key,value in items:
        key=key.lower()
        if key not in HEADERS:continue
        if key in out:raise EvidenceError('duplicate signed header rejected')
        if not isinstance(value,str) or not value or len(value)>1000 or any(ord(c)<32 or ord(c)>126 for c in value):
            raise EvidenceError('bounded original signature header required')
        out[key]=value
    if set(out)!=set(HEADERS):raise EvidenceError('five original PayPal signature headers required')
    return out

def store(spool,raw,items):
    if not 0<len(raw)<=MAX_BODY:raise EvidenceError('bounded raw body required')
    parse_raw(raw);headers=original_headers(items)
    envelope={'format':'item6-paypal-raw-v1','raw_body_b64':base64.b64encode(raw).decode('ascii'),'headers':headers,
              'raw_sha256':hashlib.sha256(raw).hexdigest(),'received_unix':int(time.time()),'verified':False,'sandbox_only':True}
    blob=(json.dumps(envelope,separators=(',',':'))+'\n').encode()
    with LOCK:
        spool=private_dir(spool)
        files=list(spool.iterdir())
        if any(p.is_symlink() or not p.is_file() or stat.S_IMODE(p.stat().st_mode)&0o077 for p in files):raise EvidenceError('unsafe spool entry; preserve and review')
        if len(files)>=MAX_FILES or sum(p.stat().st_size for p in files)+len(blob)>MAX_DISK:raise EvidenceError('spool capacity reached; preserve and review')
        receipt=uuid.uuid4().hex
        target=spool/(receipt+'.json')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
            dfd=os.open(spool,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        except Exception:
            # Never remove partial evidence automatically. Disk errors need review.
            raise EvidenceError('durable write failed; inspect private spool') from None
        return receipt

class Server(http.server.ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,addr,spool):
        self.spool=private_dir(spool);self.slots=threading.BoundedSemaphore(8)
        super().__init__(addr,Handler)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            request.close();return
        try:super().process_request(request,client_address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()
    def handle_error(self,request,client_address):pass

class Handler(http.server.BaseHTTPRequestHandler):
    server_version='EvidenceReceiver';sys_version=''
    def setup(self):
        super().setup();self.connection.settimeout(15)
    def log_message(self,*args):pass
    def log_error(self,*args):pass
    def reply(self,status,category):
        blob=json.dumps({'category':category,'verified':False,'payment_effect':False}).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(blob)));self.send_header('Connection','close');self.end_headers();self.wfile.write(blob);self.close_connection=True
    def do_GET(self):
        self.reply(200 if self.path=='/health' else 404,'passive sandbox receiver' if self.path=='/health' else 'not found')
    def do_POST(self):
        if self.path!=PATH:return self.reply(404,'not found')
        try:
            if self.headers.get_all('Transfer-Encoding'):raise EvidenceError('transfer encoding rejected')
            sizes=self.headers.get_all('Content-Length') or []
            if len(sizes)!=1 or not re.fullmatch(r'[0-9]{1,6}',sizes[0]):raise EvidenceError('one bounded Content-Length required')
            size=int(sizes[0])
            if not 0<size<=MAX_BODY:return self.reply(413,'bounded raw body required')
            types=self.headers.get_all('Content-Type') or []
            if len(types)!=1 or types[0].split(';')[0].strip().lower()!='application/json':raise EvidenceError('JSON media type required')
            raw=self.rfile.read(size)
            if len(raw)!=size:raise EvidenceError('incomplete body')
            store(self.server.spool,raw,self.headers.raw_items())
            self.reply(200,'durably stored; unverified')
        except EvidenceError as error:
            self.reply(503 if any(x in str(error) for x in ('capacity','unsafe','durable')) else 400,str(error))
        except (OSError,ValueError):
            self.reply(503,'private evidence storage/request failure')

def decode_envelope(envelope):
    if not isinstance(envelope,dict) or set(envelope)!={'format','raw_body_b64','headers','raw_sha256','received_unix','verified','sandbox_only'}:
        raise EvidenceError('exact private raw envelope required')
    if envelope['format']!='item6-paypal-raw-v1' or envelope['verified'] is not False or envelope['sandbox_only'] is not True:raise EvidenceError('unverified sandbox envelope required')
    try:raw=base64.b64decode(envelope['raw_body_b64'],validate=True)
    except (ValueError,TypeError):raise EvidenceError('valid bounded raw body encoding required') from None
    if not 0<len(raw)<=MAX_BODY or hashlib.sha256(raw).hexdigest()!=envelope['raw_sha256']:raise EvidenceError('exact raw body hash required')
    parse_raw(raw)
    headers=original_headers(envelope['headers'].items())
    return raw,headers

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('serve','export','status'));p.add_argument('--spool',type=Path,default=Path('/var/lib/requi-receiver/spool'));p.add_argument('--receipt');p.add_argument('--port',type=int,default=8787);a=p.parse_args()
    try:
        spool=private_dir(a.spool)
        if a.action=='serve':Server(('127.0.0.1',a.port),spool).serve_forever()
        elif a.action=='status':
            entries=list(spool.iterdir());print(json.dumps({'checked':'private evidence spool','expected':'bounded passive sandbox receipts','found':{'files':len(entries),'bytes':sum(f.stat().st_size for f in entries)},'where':str(spool),'payment_effect':False,'verified':False}))
            for f in sorted(entries):
                if re.fullmatch(r'[a-f0-9]{32}\.json',f.name):print(json.dumps({'receipt':f.stem,'verified':False}))
        else:
            if not a.receipt or not re.fullmatch(r'[a-f0-9]{32}',a.receipt):raise EvidenceError('exact local receipt ID required')
            path=spool/(a.receipt+'.json')
            if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077 or path.stat().st_size>400000:raise EvidenceError('private bounded receipt required')
            envelope=json.loads(path.read_bytes());decode_envelope(envelope)
            # Export is explicitly requested over authenticated SSH, not public HTTP.
            sys.stdout.write(json.dumps(envelope,separators=(',',':'))+'\n')
    except Exception as error:
        category=str(error) if isinstance(error,EvidenceError) else type(error).__name__+'; values not printed'
        print(json.dumps({'checked':'receiver '+a.action,'expected':'private bounded sandbox evidence','found':category,'where':'private receiver spool','payment_effect':False}),file=sys.stderr);return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
