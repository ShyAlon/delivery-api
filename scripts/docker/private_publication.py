"""Private GHCR publication of an already accepted immutable local image.

Owner invokes this stage explicitly. Uses existing local Docker and gh login,
never accepts a token in arguments, settings, logs or receipt. No server contact.
"""
from __future__ import annotations
import json
import re
from urllib.parse import quote
from image_receipt import DIGEST, REPOSITORY, InvalidReceipt, digest, object_json, validate_manifest
from image_workflow import WorkflowError, command, inspect_image, log


def require_private(repository: str) -> None:
    if not REPOSITORY.fullmatch(repository):raise WorkflowError('exact private GHCR repository required')
    _,owner,package=repository.split('/',2)
    # Existing package must already be private. Do not publish a new package
    # whose visibility is unknown, and never silently change its audience.
    result=object_json(command(['gh','api','/users/'+owner+'/packages/container/'+quote(package,safe='')]).encode())
    if result.get('package_type')!='container' or result.get('visibility')!='private' or result.get('name')!=package:
        raise WorkflowError('GHCR package private visibility unverified; nothing published')
    account=result.get('owner',{})
    if type(account) is not dict or str(account.get('login','')).lower()!=owner:
        raise WorkflowError('GHCR package owner differs')


def publish(repository: str, state, accepted: dict) -> dict:
    require_private(repository)
    if accepted.get('image_id')!=state.state['stages']['build']['image_id']:
        raise WorkflowError('accepted image differs from built image')
    image_id=accepted['image_id']
    if inspect_image(image_id)['Id']!=image_id:raise WorkflowError('accepted image unavailable')
    tag=repository+':accepted-'+state.key.split(':')[1][:24]
    log('INFO','stage 4/6 publish: existing private GHCR package, same accepted local image')
    command(['docker','tag',image_id,tag])
    # A tag may move in another terminal. Inspect immediately before pushing,
    # then bind the registry bytes back to the locally accepted config digest.
    if inspect_image(tag)['Id']!=image_id:raise WorkflowError('publication tag changed before push')
    command(['docker','push',tag],timeout=3600)
    state.complete('publish',{'repository':repository,'tag':tag,'image_id':image_id})
    # --raw stdout contains the exact manifest bytes. No JSON reserialization.
    raw=command_raw(['docker','buildx','imagetools','inspect','--raw',tag])
    manifest_digest=digest(raw)
    validate_manifest(raw,manifest_digest,image_id)
    # Confirm immutable lookup resolves to the same bytes, not merely a tag read.
    immutable=command_raw(['docker','buildx','imagetools','inspect','--raw',repository+'@'+manifest_digest])
    validate_manifest(immutable,manifest_digest,image_id)
    require_private(repository)
    evidence={'repository':repository,'manifest_digest':manifest_digest,'config_digest':image_id,'platform':'linux/amd64'}
    state.complete('verify-manifest',evidence)
    log('PASS','single-platform private manifest matches locally tested config digest')
    return evidence


def command_raw(args: list[str]) -> bytes:
    import subprocess
    try:result=subprocess.run(args,capture_output=True,timeout=120,check=False)
    except (OSError,subprocess.TimeoutExpired):raise WorkflowError('registry manifest bytes unavailable') from None
    if result.returncode or not result.stdout or len(result.stdout)>4_000_000:
        raise WorkflowError('registry manifest bytes unavailable or oversized')
    # buildx may append a display newline. Digest verification must use original
    # registry bytes; do not guess trimming. Caller rejects if immutable lookup
    # cannot address the resulting digest.
    return result.stdout
