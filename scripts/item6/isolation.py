"""Test-only acceptance boundaries. Not imported or mounted by application code."""
from dataclasses import dataclass
from pathlib import Path
import hashlib,json,os,re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RunIsolation:
    root:Path
    database_name:str
    database_host:str
    run_id:str
    mode:str='hermetic'
    paypal_origin:str='https://api-m.sandbox.paypal.com'
    launch_authorized:bool=False

    def validate(self):
        root=Path(self.root).absolute()
        if root==Path('/') or root==Path.home() or any(p.is_symlink() for p in (root,*root.parents)):
            raise ValueError('new non-symlink isolated scratch root required')
        if root.exists():raise ValueError('new scratch root required; no replay or overwrite')
        if not re.fullmatch(r'requi_item6_[a-z0-9_]{8,60}',self.database_name):
            raise ValueError('explicit isolated item6 database name required')
        if self.database_host not in ('127.0.0.1','localhost','::1'):
            raise ValueError('loopback disposable database required; no production host')
        if not re.fullmatch(r'[a-f0-9]{32}',self.run_id):raise ValueError('fresh bounded run identifier required')
        if self.mode not in ('hermetic','mac_acceptance'):raise ValueError('explicit test-only mode required')
        if self.paypal_origin!='https://api-m.sandbox.paypal.com' or self.launch_authorized is not False:
            raise ValueError('sandbox-only run; production launch authority forbidden')
        return self

    def initialize(self):
        self.validate();root=Path(self.root).absolute();root.mkdir(mode=0o700)
        self.write(root/'run-scope.json',{'kind':'item6-isolated-acceptance','run_id':self.run_id,
            'database_name':self.database_name,'database_host':self.database_host,'mode':self.mode,
            'paypal_origin':self.paypal_origin,'production_routes_enabled':False,'launch_authorized':False})
        return root

    @staticmethod
    def write(path,data):
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(data,f,sort_keys=True,indent=2)


def safe_model_route(provider,model,price_catalog_verified):
    # These checks do not establish spending approval. Mac invocation has its
    # separate go. Never route through an env-selected default/other provider.
    if provider!='deepseek' or not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',model):
        raise ValueError('explicit DeepSeek model required')
    if price_catalog_verified is not True:raise ValueError('reviewed current catalog required before any paid call')
    return {'provider':provider,'model':model}


def parse_loopback_service(service_text,name):
    """Credential file stays local. Inspect destination only; never print text."""
    import configparser
    parser=configparser.ConfigParser(interpolation=None);parser.read_string(service_text)
    if name not in parser:raise ValueError('explicit service entry required')
    entry=parser[name]
    allowed={'host','port','dbname','user','password','sslmode','connect_timeout'}
    if set(entry)-allowed:raise ValueError('unknown service option could redirect connection')
    if entry.get('host') not in ('127.0.0.1','localhost','::1'):
        raise ValueError('disposable loopback database required')
    db=entry.get('dbname','')
    if not re.fullmatch(r'requi_item6_[a-z0-9_]{8,60}',db):raise ValueError('isolated database name required')
    port=entry.get('port','5432')
    if not port.isdigit() or not 1<=int(port)<=65535:raise ValueError('explicit valid local port required')
    return {'host':entry['host'],'database_name':db,'port':int(port)}
