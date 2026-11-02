"""PR acceptance cannot silently depend on the manual package credential."""
from pathlib import Path
import hashlib
import yaml


def test_pr_acceptance_is_secretless_real_pinned_producers():
    root=Path(__file__).parents[1]
    job=yaml.safe_load((root/'.github/workflows/ci-cd.yml').read_text())['jobs']['fresh-start-acceptance']
    text=str(job)
    assert 'ITEM6_SOURCE_READ_TOKEN' not in text and 'acceptance-sources' not in text
    assert 'fresh-start-acceptance.py' in text and 'sha256sum --check' in text
    for line in (root/'vendor/acceptance-producers.sha256').read_text().splitlines():
        digest,path=line.split();assert hashlib.sha256((root/path).read_bytes()).hexdigest()==digest
        assert path in text
