"""Minimal public smoke context, shared by Docker smoke and its CI proof."""
from pathlib import Path
import shutil,sys

FIXED=('tests/pg_harness.py','tests/pg_guardian.py','src/delivery_api/runtime_roles.py',
       'scripts/docker/owned-pg-smoke.py','scripts/docker/system-pg-smoke.py','scripts/docker/Dockerfile.pg-smoke')


def prepare(root,destination):
    for relative in (*FIXED,*(str(p.relative_to(root)) for p in sorted((root/'supabase/migrations').glob('*.sql')))):
        source=root/relative
        if not source.is_file():raise ValueError('Missing smoke prerequisite: '+relative)
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target);target.chmod(0o644)


if __name__=='__main__':prepare(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
