"""Owner-only bounded Ollama diagnostic, no model loads or mutations."""
import json,subprocess,inspect
from pathlib import Path
from urllib.parse import urlsplit
from delivery_api.ollama_readiness import probe

def diagnose(source,home,*,base=None,model=None):
    from image_settings import read_values
    from local_pg_preview import identity,names,inspect_owned
    values=read_values(Path(source).expanduser().absolute())
    base_override=base;model_override=model
    base=base or values.get('OLLAMA_BASE_URL') or 'http://host.docker.internal:11434/v1'
    model=model or values.get('DELIVERY_OLLAMA_MODEL')
    providers=values.get('REQUI_PIN_PROVIDERS','deepseek').split(',')
    report={'read_only':True,'source_path':str(Path(source).expanduser().absolute()),'ollama_provider_selected':'ollama' in providers,
      'model_selected':bool(model),'memory_fit':'unverified','app_container_limit_bytes':3*1024**3,'approved_pin_note':'Known local packages are selected through the app registry. No launch-model setting or operator pin is required. This diagnostic does not load a model or prove memory fit.',
      'memory_note':'The 3GB limit is for Requi.re, not native Mac Ollama. File/list size is not runtime RAM. Context/KV cache and other apps matter. No model was loaded; prior phi4 failure is not a fit pass.'}
    host=probe('http://127.0.0.1:11434/v1',model)
    report['mac_loopback_ollama']=host
    try:
        memory=subprocess.run(['sysctl','-n','hw.memsize'],capture_output=True,text=True,timeout=3)
        if memory.returncode==0:report['mac_physical_ram_bytes']=int(memory.stdout.strip())
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    path=Path(home).expanduser()/'identity.json'
    if not path.exists():report['container']={'reason':'owned_app_not_started'};return report
    # Read existing nonce only. The identity helper cannot create because path exists.
    nonce=identity(path);resource=names(nonce);app=inspect_owned('container',resource['app'],nonce)
    if not app or not app.get('State',{}).get('Running'):report['container']={'reason':'owned_app_not_running'};return report
    from delivery_api import ollama_readiness as reader
    code=inspect.getsource(reader)+'''
base=sys.argv[1] or os.environ.get('OLLAMA_BASE_URL') or 'http://localhost:11434/v1'
model=sys.argv[2] or os.environ.get('DELIVERY_OLLAMA_MODEL')
r=probe(base,model)
r['ollama_provider_selected']='ollama' in os.environ.get('REQUI_PIN_PROVIDERS','deepseek').split(',')
r['model_selected']=bool(model)
r['selected_model_id']=model if isinstance(model,str) and __import__('re').fullmatch(r'[a-zA-Z0-9._:/-]{1,200}',model) else None
r['endpoint_topology']='container_loopback' if urlsplit(base).hostname in ('localhost','127.0.0.1','::1') else 'docker_host' if urlsplit(base).hostname=='host.docker.internal' else 'configured_server'
print(json.dumps(r))
'''
    try:result=subprocess.run(['docker','exec','--interactive',resource['app'],'python','-',base_override or '',model_override or ''],input=code,capture_output=True,text=True,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        report['container']={'reason':'container_probe_unconfirmed'};return report
    try:report['container']=json.loads(result.stdout) if result.returncode==0 else {'reason':'container_probe_failed'}
    except ValueError:report['container']={'reason':'container_probe_invalid'}
    missing=[]
    current=report['container']
    # Launch defaults are historical diagnostics, not selection prerequisites.
    if current.get('endpoint_topology')=='container_loopback':missing.append('running app OLLAMA_BASE_URL points inside container, not Mac; use host.docker.internal for native Mac Ollama')
    if not current.get('reachable'):missing.append('Ollama not reachable from running app; verify service/bind/firewall and Docker host route')
    elif current.get('model_selected') and not current.get('selected_model_present'):missing.append('selected model is not installed at the app endpoint')
    report['missing']=missing
    return report
