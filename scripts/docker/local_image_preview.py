"""Owner-started preview of the exact locally accepted immutable image."""
import json
from pathlib import Path
import time
from image_workflow import WorkflowError, command, inspect_image, log, confirm
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
    from image_settings import read_values,validate,write_container_env
    try:
        known=read_values(settings)
        values=validate(settings,generate_missing_token=apply)
    except ValueError as error:raise WorkflowError(str(error)) from None
    if apply:
        for key in ('INTERVIEW_ENGINE_TOKEN','OLLAMA_BASE_URL'):
            if key in values and not known.get(key):
                log('INFO','derived and saved '+key+' in '+str(settings)+'; reused on later starts'+('; the value is never shown' if key=='INTERVIEW_ENGINE_TOKEN' else ''))
    # Pass exactly these owner settings values, no filtered environment or mode.
    args=Target(image_id,name,str(settings),port,network).arguments()
    log('INFO','preview review: accepted linux/amd64 image, existing settings, same shared runtime; work may spend only after explicit Start')
    if not apply:return {'state':'reviewed','image_id':image_id,'applied':False}
    if not confirm('start this accepted preview (no task is started)'):
        raise WorkflowError('preview declined; nothing started')
    # docker --env-file passes values literally (no quote stripping), so hand the
    # container the exact parsed values validate approved, not the owner file.
    container_env=state.path.parent/'preview-container.env'
    write_container_env(container_env,values)
    args=Target(image_id,name,str(container_env),port,network).arguments()
    # No silent replacement, force-stop, schema apply or host-volume migration.
    import subprocess
    exists=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=60)
    if exists.returncode==0:
        running=None
        try:running=json.loads(exists.stdout)[0].get('State',{}).get('Running')
        except (ValueError,IndexError,AttributeError):pass
        if running is True:
            raise WorkflowError('preview container already exists and is running; inspect it, then stop the reviewed container before retrying: docker stop '+name)
        raise WorkflowError('preview container already exists from an earlier run; inspect its failure: docker logs --tail 80 '+name+'; then remove it before retrying: docker rm '+name)
    args[1:1]=['--label','requi.shared-runtime=1']
    command(['docker',*args])
    last={}
    for _ in range(120):
        actual=json.loads(command(['docker','container','inspect','--format','{{json .}}',name]))
        if actual.get('Image')!=image_id:raise WorkflowError('preview running image differs; inspect before retry')
        status=actual.get('State',{});last=status
        if status.get('Health',{}).get('Status')=='healthy':
            from preview_status import summarize
            status=summarize({'ok':True})
            log('WARN',status['message'])
            log('PASS','preview healthy at http://127.0.0.1:'+str(port)+'; exact local image retained')
            # Preserve the real browser gate, including named expected anonymous
            # 401 classification. Never infer readiness from health alone.
            browser=Path(__file__).resolve().parents[1]/'ops/browser_smoke.py'
            result=subprocess.run([__import__('sys').executable,str(browser),'http://127.0.0.1:'+str(port)],
                                  capture_output=True,text=True,timeout=90)
            output=state.path.parent/'browser-smoke.log'
            descriptor=__import__('os').open(output,__import__('os').O_WRONLY|__import__('os').O_CREAT|__import__('os').O_TRUNC,0o600)
            with __import__('os').fdopen(descriptor,'w') as stream:stream.write(result.stdout+result.stderr)
            if result.returncode:
                log('FAIL','browser smoke failed; named browser evidence in '+str(output)+'; container retained: '+name)
                raise WorkflowError('preview browser boot unverified; inspect docker logs '+name+' and private browser-smoke.log, no automatic restart')
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
    code=last.get('ExitCode')
    detail='exit code '+str(code) if code is not None else 'exit code unknown'
    raise WorkflowError('preview readiness failed ('+detail+'); the typed startup problem is in the container log: docker logs --tail 80 '+name+'; owned container retained for inspection, no task Start sent')
