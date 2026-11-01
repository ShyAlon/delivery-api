import json,subprocess,sys
from pathlib import Path
SCRIPT=Path(__file__).parents[1]/'scripts/ops/preview-container-identity.py'

def invoke(c):return subprocess.run([sys.executable,str(SCRIPT),'8001','requi-preview-data'],input=json.dumps([c]),text=True,capture_output=True)
def own():return {'Id':'owned-id','Name':'/requi-preview-exec','Config':{'Image':'requi-full:candidate'},'Mounts':[{'Type':'volume','Name':'requi-preview-data','Destination':'/data'}],'HostConfig':{'PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8001'}]}}}
def test_exact_owned_identity_and_wrong_image_port_volume_refuse():
    assert invoke(own()).stdout=='OWNED owned-id\n'
    for field in ('image','port','volume','name'):
        c=own()
        if field=='image':c['Config']['Image']='unrelated:latest'
        elif field=='port':c['HostConfig']['PortBindings']['8000/tcp'][0]['HostPort']='9999'
        elif field=='volume':c['Mounts'][0]['Name']='other-data'
        else:c['Name']='/other'
        result=invoke(c);assert result.returncode==1 and result.stdout=='UNKNOWN\n'
