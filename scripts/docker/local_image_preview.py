"""Owner-started preview of the exact locally accepted immutable image."""
import json
from pathlib import Path
import time
from image_workflow import WorkflowError, command, inspect_image, log
from runtime_spec import Target


def preview(state,*,env_file,name='requi-preview-exec',port=8001,network='bridge',backoffice_port=8020,apply=False):
    accepted=state.state['stages'].get('local-acceptance',{})
    built=state.state['stages'].get('build',{})
    image_id=built.get('image_id')
    if not image_id or accepted.get('image_id')!=image_id or accepted.get('runtime_contract')!=state.inputs['runtime_contract']:
        raise WorkflowError('preview requires exact current local acceptance; run --accept-local first')
    if accepted.get('checks')!={name:'passed' for name in ('startup','signed-http','child-lifecycle','stop','retained-outputs')}:
        raise WorkflowError('preview acceptance incomplete')
    if inspect_image(image_id)['Id']!=image_id:raise WorkflowError('accepted local preview image unavailable')
    settings=Path(env_file).expanduser().resolve()
    if not settings.is_file() or settings.stat().st_mode&0o077:
        raise WorkflowError('preview requires existing private settings file; no automatic migration or setup')
    # Pass exactly these owner settings values, no filtered environment or mode.
    args=Target(image_id,name,str(settings),port,network).arguments()
    log('INFO','preview review: accepted linux/amd64 image, existing settings, same shared runtime; work may spend only after explicit Start')
    if not apply:return {'state':'reviewed','image_id':image_id,'applied':False}
    if input('Type yes to start this accepted preview (no task is started): ').strip()!='yes':
        raise WorkflowError('preview declined; nothing started')
    # No silent replacement, force-stop, schema apply or host-volume migration.
    import subprocess
    exists=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=60)
    if exists.returncode==0:
        raise WorkflowError('preview container already exists; inspect and stop the reviewed container before retrying')
    args[1:1]=['--label','requi.shared-runtime=1']
    command(['docker',*args])
    for _ in range(120):
        actual=json.loads(command(['docker','container','inspect','--format','{{json .}}',name]))
        if actual.get('Image')!=image_id:raise WorkflowError('preview running image differs; inspect before retry')
        status=actual.get('State',{})
        if status.get('Health',{}).get('Status')=='healthy':
            log('PASS','preview healthy at http://127.0.0.1:'+str(port)+'; exact local image retained')
            from private_backoffice import PrivateBackoffice
            backoffice=PrivateBackoffice(backoffice_port,port,state.path.parent/'backoffice.log')
            try:
                backoffice.start()
                log('PASS','private Back office at http://127.0.0.1:'+str(backoffice_port)+'; Ctrl-C closes this listener, preview container is retained')
                while backoffice.process.poll() is None:time.sleep(.5)
                raise WorkflowError('private Back office exited; preview retained for inspection')
            except KeyboardInterrupt:
                return {'state':'healthy','image_id':image_id,'container':name}
            finally:backoffice.close()
        if status.get('Running') is not True or status.get('Health',{}).get('Status')=='unhealthy':break
        time.sleep(1)
    raise WorkflowError('preview readiness failed; owned container retained for inspection, no task Start sent')
