"""Private filesystem installation mechanics, not an acceptance surface.

No route imports this module. Trusted caller must authenticate review, hold both
writer exclusions, compare current owner/task/evidence and journal every effect.
Failure retains bytes; retry/cleanup is never automatic.
"""
import ctypes,errno,hashlib,json,os,re,stat
from pathlib import Path
from .producer_inputs import _open_directory,_identity


def _validate_plan(plan,plan_sha256):
    from vmodel_contracts.execution_anchor import ExecutionAnchor
    if (not isinstance(plan,dict) or hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=plan_sha256
        or plan.get('kind')!='closed_revision_installation_effect_plan' or plan.get('state')!='planned'
        or any(plan.get(k) is not False for k in ('accepted','installed','dispatch_started','automatic_replay_allowed','source_deletion_allowed','copy_stop_marker'))):
        raise ValueError('exact held non-effectful installation plan required')
    edited=plan.get('edited_gate')
    if not isinstance(edited,dict) or edited!={'phase':edited.get('phase'),'state':'decided','approval_reset':False,'approved_by_exact_acceptance':True}:
        raise ValueError('exact accepted phase approval plan required')
    document=plan['destination_anchor']
    if document.get('kind')!='installed_execution_generation_anchor' or document.get('version')!=1:raise ValueError('exact prospective anchor required')
    anchor=ExecutionAnchor(**{k:v for k,v in document.items() if k not in ('kind','version')})
    if anchor.document()!=document:raise ValueError('canonical prospective anchor required')
    parent=plan['expected_parent']
    if (parent['owner_id']!=anchor.owner_id or parent['task_id']!=anchor.task_id or parent['generation']+1!=anchor.generation
        or parent['execution_session_id']==anchor.execution_session_id):raise ValueError('exact parent/anchor binding required')
    return anchor


def prepare_execution_anchor(*,stage_root,plan,plan_sha256,validate_authority):
    """Write/fsync anchor exclusively in private staged root, never bind a run.

    This is a separately journaled effect after authenticated acceptance. The
    callback must validate that authority, not an untrusted client boolean.
    Presence of this file never establishes installed control-plane authority.
    """
    if not callable(validate_authority):raise ValueError('trusted installation authority required')
    anchor=_validate_plan(plan,plan_sha256);root=Path(stage_root)
    expected=('tenants',anchor.owner_id,'sessions',plan['expected_parent']['execution_session_id'],'spec','revision-proposals')
    if (not root.is_absolute() or '..' in root.parts or root.parts[-9:-3]!=expected
        or not re.fullmatch('[a-f0-9]{32}',root.parent.parent.name)
        or root.name!=anchor.execution_session_id or root.parent.name!='staged'):
        raise ValueError('private staged root required')
    parent=_open_directory(root)
    try:
        validate_authority()
        for name in ('execution-generation.json',):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:continue
            raise ValueError('anchor already present; explicit reconciliation required')
        interview=os.open('interview',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            try:os.stat('stop-request.json',dir_fd=interview,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise ValueError('fresh namespace must not copy a Stop marker')
        finally:os.close(interview)
        raw=json.dumps(anchor.document(),sort_keys=True,indent=2).encode()
        validate_authority()
        fd=os.open('execution-generation.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(fd)
        finally:os.close(fd)
        os.fsync(parent);validate_authority()
        current=_open_directory(root)
        try:
            if _identity(current)!=_identity(parent):raise ValueError('stage namespace changed across anchor write')
        finally:os.close(current)
        return {'anchor_sha256':hashlib.sha256(raw).hexdigest(),'installed':False,'dispatch_started':False}
    finally:os.close(parent)


def promote_private_stage(*,artifacts_base,lineage,plan,plan_sha256,validate_authority):
    """Linux same-filesystem no-overwrite rename; no DB activation or retry.

    Trusted caller journals promotion_pending before this call, then validates
    promoted files/anchor and journals files_promoted. Any ambiguous outcome is
    held for explicit reconciliation, not passed to automatic execution.
    """
    from .tenancy import TenantScope,session_root
    from vmodel_contracts.execution_anchor import read_execution_anchor
    if not callable(validate_authority):raise ValueError('trusted installation authority required')
    anchor=_validate_plan(plan,plan_sha256)
    if (lineage.get('owner_id')!=anchor.owner_id or lineage.get('task_id')!=anchor.task_id
        or lineage.get('execution_session_id')!=anchor.execution_session_id
        or lineage.get('parent_execution_session_id')!=plan['expected_parent']['execution_session_id']
        or hashlib.sha256(json.dumps(lineage,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=anchor.lineage_sha256):
        raise ValueError('promotion lineage differs from plan')
    scope=TenantScope(anchor.owner_id)
    source=session_root(Path(artifacts_base),scope,lineage['parent_execution_session_id'])/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'
    destination=session_root(Path(artifacts_base),scope,anchor.execution_session_id)
    source_fd=_open_directory(source);sessions_fd=_open_directory(destination.parent)
    try:
        validate_authority()
        stage_fd=os.open(anchor.execution_session_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=source_fd)
        try:
            if os.fstat(stage_fd).st_dev!=os.fstat(sessions_fd).st_dev:raise ValueError('same-filesystem promotion required')
            anchor_fd=os.open('execution-generation.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=stage_fd)
            info=os.fstat(anchor_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>16384:
                os.close(anchor_fd);raise ValueError('bounded regular prepared anchor required')
            try:
                with os.fdopen(anchor_fd,'rb',closefd=False) as f:raw=f.read(16385)
                if len(raw)>16384 or json.loads(raw)!=anchor.document():raise ValueError('prepared anchor differs from install plan')
            finally:os.close(anchor_fd)
            validate_authority()
            libc=ctypes.CDLL(None,use_errno=True)
            rename=getattr(libc,'renameat2',None)
            if rename is None:raise ValueError('atomic no-overwrite promotion unsupported on this host')
            rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint);rename.restype=ctypes.c_int
            name=anchor.execution_session_id.encode()
            if rename(source_fd,name,sessions_fd,name,1)!=0:
                code=ctypes.get_errno()
                if code==errno.EEXIST:raise ValueError('destination exists; explicit reconciliation required')
                raise OSError(code,os.strerror(code))
            os.fsync(source_fd);os.fsync(sessions_fd)
            promoted=os.open(anchor.execution_session_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=sessions_fd)
            try:
                if _identity(promoted)!=_identity(stage_fd):raise ValueError('promoted namespace identity changed')
            finally:os.close(promoted)
            read_execution_anchor(destination,expected=anchor);validate_authority()
            return {'anchor_sha256':hashlib.sha256(raw).hexdigest(),'files_promoted':True,'installed':False,'dispatch_started':False}
        finally:os.close(stage_fd)
    finally:os.close(source_fd);os.close(sessions_fd)
