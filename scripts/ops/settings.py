#!/usr/bin/env python3
"""Private file-first settings boundary for owner and operations commands.

Never source dotenv as shell, interpolate values, or print values. Only known
configuration names cross this boundary. No connection or mutation is made here.
"""
import argparse
import termios
import os
from pathlib import Path
import re
import sys
import tempfile
from dotenv.parser import parse_stream
from dotenv import set_key

EXACT={'DEEPSEEK_API_KEY','RESET_OWNER_ID','RESET_AUTH_SESSION_ID','REQUI_ADMIN_DATABASE_URL',
       'INTERVIEW_ENGINE_MODEL_PROBING','INTERVIEW_ENGINE_MODEL_DISTILLATION',
       'OLLAMA_BASE_URL','OLLAMA_HOST','VMODEL_OLLAMA_NUM_CTX'}
IDENTITY=('DELIVERY_DATABASE_URL','DELIVERY_SUPABASE_URL')

class SettingsError(ValueError):
    pass

def allowed(name):
    return name in EXACT or bool(re.fullmatch(r'DELIVERY_[A-Z0-9_]+',name)) and not name.startswith('DELIVERY_TEST_')

def read_file(path):
    path=Path(path)
    if path.is_symlink():raise SettingsError('settings file must not be a symlink: '+str(path))
    if not path.exists():return {}
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise SettingsError('settings file must be private (chmod 600): '+str(path))
    try:
        with path.open(encoding='utf-8') as stream:bindings=list(parse_stream(stream))
    except Exception as exc:raise SettingsError('cannot read settings ('+type(exc).__name__+'): '+str(path)) from None
    result={}
    for b in bindings:
        if b.error:raise SettingsError('invalid settings syntax at line '+str(b.original.line)+': '+str(path))
        if b.key:
            if b.key in result:raise SettingsError('duplicate setting name: '+b.key)
            result[b.key]=b.value or ''
    return result

def target_path(env=None):
    env=os.environ if env is None else env
    return Path(env.get('PREVIEW_SETTINGS_FILE') or str(Path(env.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'requi/preview.env')).expanduser()

class SettingsValues(dict):
    pass

def source_report(root,name,env=None,explicit=None):
    env=os.environ if env is None else env
    paths=[Path(explicit).expanduser()] if explicit else [target_path(env),Path(root)/'.env']
    states=[];selected=None
    for path in paths:
        values=read_file(path)
        state='file missing' if not path.exists() else 'name absent' if name not in values else 'name present but empty' if not values[name] else 'name present'
        states.append(str(path)+' ['+state+']')
        if values.get(name) and selected is None:selected=str(path)
    if not selected and env.get(name):selected='shell fallback'
    return name+' source: '+(selected or 'MISSING')+'; checked: '+'; '.join(states)+'; values hidden'

def load(root,env=None,explicit=None):
    env=os.environ if env is None else env
    paths=[Path(explicit).expanduser()] if explicit else [target_path(env),Path(root)/'.env']
    if explicit and not paths[0].is_file():
        raise SettingsError('explicit settings file is missing: '+str(paths[0]))
    result=SettingsValues();result.context=(root,dict(env),explicit);seen=[]
    for path in paths:
        if path.resolve() in seen:continue
        seen.append(path.resolve());values=read_file(path)
        for name in IDENTITY:
            if result.get(name) and values.get(name) and result[name]!=values[name]:
                raise SettingsError('project settings conflict for '+name+'; align the files or select --env-file explicitly')
        for name,value in values.items():
            if allowed(name) and value and name not in result:result[name]=value
    # Environment is last, but never fill a partial file-based project identity
    # from an unrelated shell project. Provider keys and explicit owner IDs may fall back.
    file_identity=any(result.get(k) for k in IDENTITY)
    for name,value in env.items():
        if allowed(name) and value and name not in result:
            if name.startswith('DELIVERY_SUPABASE_') or name in IDENTITY:
                if file_identity:continue
            result[name]=value
    return result

def terminal_line(prompt,hidden=False):
    # Buffered r+ requires seekability and fails on actual terminal devices.
    # Use a raw descriptor; never let a secret prompt fall back to echoed stdin.
    path=os.environ.get('PREVIEW_TTY','/dev/tty');fd=None;original=None
    try:
        fd=os.open(path,os.O_RDWR|os.O_NOCTTY)
        if not os.isatty(fd):raise SettingsError('input requires a terminal: '+path)
        if hidden:
            original=termios.tcgetattr(fd);changed=list(original)
            changed[3]&=~termios.ECHO
            termios.tcsetattr(fd,termios.TCSAFLUSH,changed)
        os.write(fd,prompt.encode())
        raw=bytearray()
        while True:
            char=os.read(fd,1)
            if not char:raise SettingsError('terminal input ended: '+path)
            if char in (b'\n',b'\r'):break
            raw.extend(char)
        return raw.decode('utf-8')
    except OSError as exc:
        raise SettingsError('hidden input requires a terminal; cannot open '+path+' ('+type(exc).__name__+'); use a private settings file') from None
    finally:
        if fd is not None:
            if original is not None:
                termios.tcsetattr(fd,termios.TCSADRAIN,original);os.write(fd,b'\n')
            os.close(fd)

def hidden_prompt(name):
    try:return terminal_line(name+' (hidden, missing from settings): ',hidden=True)
    except SettingsError as exc:raise SettingsError('missing '+name+'; '+str(exc)) from None

def require(values,names,prompt=None):
    prompt=prompt or hidden_prompt
    for name in names:
        if not values.get(name):
            if hasattr(values,'context'):
                root,env,explicit=values.context
                print(source_report(root,name,env,explicit),file=sys.stderr)
            value=prompt(name).strip()
            if not value:raise SettingsError('required setting is missing: '+name)
            if any(c in value for c in '\n\r\x00'):raise SettingsError('setting must be one line: '+name)
            values[name]=value
    for name in names:
        if name in ('DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL') and not values[name].startswith(('postgresql://','postgres://')):
            raise SettingsError(name+' must start with postgres:// or postgresql://')
        if name=='DELIVERY_SUPABASE_URL' and not values[name].startswith('https://'):
            raise SettingsError(name+' must start with https://')

def terminal_confirm(prompt):
    return terminal_line(prompt).strip()

def save_confirmed(path,values,confirm=None):
    confirm=confirm or terminal_confirm
    if confirm('Type yes to save these settings locally at '+str(path)+' (mode 600), Enter to keep them only for this run: ')!='yes':return False
    path=Path(path);existing=read_file(path)
    existing.update({k:v for k,v in values.items() if allowed(k)})
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.requi-settings-',dir=path.parent)
    try:
        os.close(fd);os.chmod(name,0o600)
        for key,value in existing.items():set_key(name,key,value,quote_mode='always')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    return True

def delivery_env(destination):
    excluded={'DELIVERY_CLOUD_EXECUTION','DELIVERY_COOKIE_SECURE','DELIVERY_PREVIEW','DELIVERY_LOCAL_PREVIEW','DELIVERY_METRICS_DB','DELIVERY_HOST','DELIVERY_PORT','DELIVERY_CONTROL_PLANE_ONLY','DELIVERY_PUBLIC_ORIGIN','DELIVERY_ALT_ORIGINS'}
    with open(destination,'w',opener=lambda p,f:os.open(p,f,0o600)) as stream:
        os.chmod(destination,0o600)
        for name,value in sorted(os.environ.items()):
            if not name.startswith('DELIVERY_') or name.startswith('DELIVERY_TEST_') or name in excluded:continue
            if re.search('DEEPSEEK|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|MISTRAL|GROQ|COHERE|XAI|PROVIDER|API_KEY|MODEL_KEY',name,re.I):continue
            if any(c in value for c in '\n\r\x00'):raise SettingsError('container setting must be one line: '+name)
            stream.write(name+'='+value+'\n')

def apply_environment(root,env=None):
    env=os.environ if env is None else env
    values=load(root,env)
    for name in list(env):
        if allowed(name):env.pop(name)
    env.update(values)
    return values

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['exec','delivery-env','provision']);p.add_argument('--repo',default=os.getcwd());p.add_argument('--require',default='');p.add_argument('--optional',default='');p.add_argument('--env-file');p.add_argument('command',nargs=argparse.REMAINDER);args=p.parse_args()
    if args.mode=='delivery-env':delivery_env(args.command[0]);return
    values=load(args.repo,explicit=args.env_file)
    before=dict(values);require(values,[k for k in args.require.split(',') if k])
    if args.mode=='provision':
        for name in filter(None,args.optional.split(',')):
            if not values.get(name):
                value=hidden_prompt(name+' optional, Enter skips').strip()
                if value:values[name]=value
        save_confirmed(target_path(),values);return
    if values!=before:save_confirmed(target_path(),{k:v for k,v in values.items() if k not in before})
    updated=dict(os.environ)
    for name in list(updated):
        if allowed(name):updated.pop(name)
    updated.update(values)
    updated['REQUI_SETTINGS_READY']='1'
    command=args.command
    if command and command[0]=='--':command=command[1:]
    if not command:raise SettingsError('missing command')
    os.execvpe(command[0],command,updated)

if __name__=='__main__':
    try:main()
    except (SettingsError,EOFError,KeyboardInterrupt) as exc:
        print('SETTINGS ABORTED: '+(str(exc) if isinstance(exc,SettingsError) else 'input cancelled')+'. No values printed.',file=sys.stderr);sys.exit(2)
