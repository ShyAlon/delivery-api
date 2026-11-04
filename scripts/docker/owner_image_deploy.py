"""Owner-invoked exact-image deployment review/apply. Dry review has no SSH."""
import getpass
import json
from pathlib import Path
import re
import shlex
import subprocess
from image_receipt import Receipt, validate_manifest
from runtime_spec import Target
from image_workflow import WorkflowError, log
from private_publication import command_raw,require_private


def deploy(*,state,repository,host,name,env_file,port,network,apply=False):
    from image_receipt import object_json
    raw=(state.path.parent/'accepted-image.json').read_bytes()
    receipt=Receipt.read(raw,sources=state.inputs['sources'],runtime_contract=state.inputs['runtime_contract'],
                         acceptance_contract=state.inputs['acceptance_contract'])
    if receipt.repository!=repository:raise WorkflowError('deploy repository differs from accepted receipt')
    require_private(repository)
    manifest=command_raw(['docker','buildx','imagetools','inspect','--raw',receipt.reference])
    validate_manifest(manifest,receipt.manifest_digest,receipt.config_digest)
    args=Target(receipt.config_digest,name,env_file,port,network).arguments()
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*',host):
        raise WorkflowError('explicit reviewed SSH user@host required')
    log('INFO','deploy review: exact accepted private manifest and shared runtime; no remote contact yet')
    if not apply:return {'state':'reviewed','reference':receipt.reference,'applied':False}
    if input('Type yes to apply this exact accepted image to '+host+': ').strip()!='yes':
        raise WorkflowError('owner declined deployment; no server contact')
    username=input('GHCR username for the server read-only token: ').strip()
    token=getpass.getpass('Read-only GHCR token (hidden, not retained): ')
    program=Path(__file__).with_name('runtime_spec.py').read_text()+'\n'+Path(__file__).with_name('remote_image_apply.py').read_text()
    plan={'reference':receipt.reference,'config_digest':receipt.config_digest,'name':name,'env_file':env_file,
          'port':port,'network':network,'username':username,'token':token,'runtime_arguments':args}
    try:
        result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',host,
                               'python3 -c '+shlex.quote(program)],input=json.dumps(plan),text=True,capture_output=True,timeout=2400)
    finally:
        plan['token']='';token=''
    if result.returncode:raise WorkflowError('remote apply failed or response unavailable; inspect current service before retry, no automatic second apply')
    outcome=object_json(result.stdout.encode())
    if outcome.get('state')!='healthy' or outcome.get('config_digest')!=receipt.config_digest or outcome.get('reference')!=receipt.reference:
        raise WorkflowError('remote final identity/readiness unconfirmed; do not retry blindly')
    log('PASS','remote reports healthy and running the exact locally tested image config')
    return outcome
