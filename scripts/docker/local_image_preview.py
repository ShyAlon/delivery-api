"""Owner-started preview of the exact locally accepted immutable image."""
import json
from pathlib import Path
import subprocess
import time
from image_workflow import WorkflowError, command, inspect_image, log, confirm
from runtime_spec import Target


def run_browser_smoke(python,script,base,*,run=None):
    """The browser gate, self-healing on the one environment failure it can
    repair: exit 2 means no Chromium for this interpreter, never a boot verdict.
    Install the browser for this exact interpreter once and retry the gate once."""
    run=run or subprocess.run  # call time, never def time: tests monkeypatch it
    result=run([python,str(script),base],capture_output=True,text=True,timeout=90)
    if result.returncode==2:
        log('WARN','browser smoke found no Chromium for the preview interpreter; installing it once and retrying the gate')
        install=run([python,'-m','playwright','install','chromium'],capture_output=True,text=True,timeout=600)
        result=run([python,str(script),base],capture_output=True,text=True,timeout=90)
        if result.returncode==2 and install.returncode:
            result=type(result)(args=result.args,returncode=2,
                                stdout=result.stdout+'\nchromium install failed: '+install.stderr[-500:],stderr=result.stderr)
    return result


def preview(state,*,env_file,name='requi-preview-exec',port=8001,network='bridge',backoffice_port=8020,apply=False):
    if not apply:return _preview(state,env_file=env_file,name=name,port=port,network=network,backoffice_port=backoffice_port,apply=False)
    from host_memory import HostMemory
    from contextlib import ExitStack
    with ExitStack() as lifetime:
        return _preview(state,env_file=env_file,name=name,port=port,network=network,backoffice_port=backoffice_port,apply=True,host_memory_factory=lambda:lifetime.enter_context(HostMemory(state.path.parent/'host-telemetry')))


def _preview(state,*,env_file,name='requi-preview-exec',port=8001,network='bridge',backoffice_port=8020,apply=False,host_memory_factory=None):
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
    if not confirm('start this accepted preview; an existing '+name+' is stopped and kept renamed for rollback (no task is started)'):
        raise WorkflowError('preview declined; nothing started')
    host_memory=host_memory_factory() if host_memory_factory is not None else None
    # docker --env-file passes values literally (no quote stripping), so hand the
    # container the exact parsed values validate approved, not the owner file.
    container_env=state.path.parent/'preview-container.env'
    write_container_env(container_env,values)
    args=Target(image_id,name,str(container_env),port,network).arguments()
    # The owner just typed yes to this exact replacement. The old container is stopped (graceful, 180 s) and RENAMED, never
    # removed: its logs and image stay for rollback and the rebuild cleanup step reviews it. No force-stop, schema apply or
    # host-volume migration. 2026-11-10: refusing here sent the owner to a manual docker stop after `req preview --apply`.
    import subprocess
    exists=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=60)
    if exists.returncode==0:
        running=None
        try:running=json.loads(exists.stdout)[0].get('State',{}).get('Running')
        except (ValueError,IndexError,AttributeError):pass
        if running is not False:
            command(['docker','stop',name],timeout=180)
        renamed=name+'-'+time.strftime('%Y%m%d-%H%M%S')
        command(['docker','rename',name,renamed])
        log('INFO','previous preview container '+('was stopped and ' if running is not False else '')+'renamed to '+renamed+'; kept for rollback and reviewed by the rebuild cleanup step')
    args[1:1]=['--label','requi.shared-runtime=1']
    if host_memory is not None:args[1:1]=host_memory.mount_arguments()
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
            result=run_browser_smoke(__import__('sys').executable,browser,'http://127.0.0.1:'+str(port))
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
