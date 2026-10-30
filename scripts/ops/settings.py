#!/usr/bin/env python3
"""Private file-first settings boundary for owner and operations commands.

Never source dotenv as shell, interpolate values, or print values. Only known
configuration names cross this boundary. No connection or mutation is made here.
"""
import argparse
import getpass
import termios
import contextlib
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
    return name in EXACT or bool(re.fullmatch(r'DELIVERY_[A-Z0-9_]+',name))

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

def load(root,env=None,explicit=None):
    env=os.environ if env is None else env
    paths=[Path(explicit).expanduser()] if explicit else [target_path(env),Path(root)/'.env']
    if explicit and not paths[0].is_file():
        raise SettingsError('explicit settings file is missing: '+str(paths[0]))
    result={};seen=[]
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

def hidden_prompt(name):
    # Never allow getpass's echoing fallback on a redirected stdin. Tests may
    # provide a private answer file through PREVIEW_TTY, like other ops prompts.
    tty=os.environ.get('PREVIEW_TTY','/dev/tty')
    try:
        with open(tty,'r+') as stream:
            if not stream.isatty():raise SettingsError('missing '+name+'; hidden input requires a terminal')
            return getpass.getpass(name+' (hidden, missing from settings): ',stream=stream)
    except OSError:raise SettingsError('missing '+name+'; hidden input requires a terminal') from None

def require(values,names,prompt=None):
    prompt=prompt or hidden_prompt
    for name in names:
        if not values.get(name):
            value=prompt(name).strip()
            if not value:raise SettingsError('required setting is missing: '+name)
            if any(c in value for c in '\n\r\x00'):raise SettingsError('setting must be one line: '+name)
            values[name]=value
    for name in names:
        if name in ('DELIVERY_DATABASE_URL','REQUI_ADMIN_DATABASE_URL') and not values[name].startswith(('postgresql://','postgres://')):
            raise SettingsError(name+' must start with postgres:// or postgresql://')
        if name=='DELIVERY_SUPABASE_URL' and not values[name].startswith('https://'):
            raise SettingsError(name+' must start with https://')

def save_confirmed(path,values,confirm=input):
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
            if not name.startswith('DELIVERY_') or name in excluded:continue
            if re.search('DEEPSEEK|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|MISTRAL|GROQ|COHERE|XAI|PROVIDER|API_KEY|MODEL_KEY',name,re.I):continue
            if any(c in value for c in '\n\r\x00'):raise SettingsError('container setting must be one line: '+name)
            stream.write(name+'='+value+'\n')

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
