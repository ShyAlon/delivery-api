"""Read Docker inspect JSON, emit only safe ownership outcome. Never inspect argv/env."""
import json,sys
try:
    items=json.load(sys.stdin);c=items[0]
    image=c.get('Config',{}).get('Image','');labels=c.get('Config',{}).get('Labels') or {}
    mounts=c.get('Mounts',[]);ports=c.get('HostConfig',{}).get('PortBindings') or {}
    owned=(c.get('Name')=='/requi-preview-exec' and image.startswith('requi-full:')
           and any(m.get('Type')=='volume' and m.get('Name')==sys.argv[2] and m.get('Destination')=='/data' for m in mounts)
           and ports.get('8000/tcp')==[{'HostIp':'127.0.0.1','HostPort':sys.argv[1]}])
    if not owned:raise ValueError('Named container ownership could not be verified')
    print('OWNED '+c['Id'])
except (ValueError,KeyError,IndexError,TypeError):
    print('UNKNOWN');sys.exit(1)
