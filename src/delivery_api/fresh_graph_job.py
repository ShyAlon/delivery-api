"""Private fresh-needs phase bridge. No service startup, route or resume."""
from .tenancy import TenantAccessDenied


def validate_job(job):
    if not job:raise KeyError('worker job')
    if job['kind']!='phase:needs':raise ValueError('fresh needs phase job required')
    options=job['execution_options']
    if not isinstance(options,dict) or set(options)-{'mode','model_route'}:
        raise ValueError('exact fresh phase options required')
    if options.get('mode','start')!='start':raise ValueError('explicit resume/redo unavailable in fresh worker')
    if 'model_route' in options:
        if not isinstance(options['model_route'],str):raise ValueError('exact queued model route required')
        from .model_routes import route_value
        route,_,name=options['model_route'].partition(':');route_value(route,name)
    return options


async def execute_fresh_needs(store,worker,job_id,*,input_factory,log_factory,progress_factory=None):
    """Dispatch only the durable phase/options into the bound in-process worker.

    Trusted factories supply owner evidence, not user-selected filesystem paths.
    Runtime must match the queued model route before any graph/file/provider work.
    Fresh SRS across a needs gate and all resume paths remain separate work.
    """
    options=validate_job(store.worker_job(job_id));results=[]
    def producer(capability,root):
        capability.validate(store)
        from spec_generator.worker_runtime import current_runtime
        runtime=current_runtime()
        if runtime is None:raise ValueError('private bound job runtime required')
        if 'model_route' in options:
            model=runtime.models.get('needs')
            if model is None or f'{model.provider}:{model.model}'!=options['model_route']:
                raise TenantAccessDenied('queued model route differs from worker runtime')
        documents=input_factory(capability,root)
        if not isinstance(documents,dict) or set(documents)!={'intent','conops','baseline'}:
            raise ValueError('exact owner input documents required')
        from spec_generator.worker_graph import run_fresh_graph
        results.append(run_fresh_graph(capability=capability,session_root=root,**documents,
            log=log_factory(capability,root),progress=progress_factory(capability,root) if progress_factory else None,
            stop_after='needs'))
    started=await worker.execute(job_id,producer)
    return {'job_id':job_id,'started':started,'results':results,'downstream_dispatch_started':False}
