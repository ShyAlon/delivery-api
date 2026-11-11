"""A typed confirmation guards a change; with nothing to change it must not ask (2026-11-10).

Shy was asked to type "APPLY 0 MIGRATIONS TO delivery_app" (and, in req rebuild, "APPLY MIGRATIONS" before it) with no
migration pending and every role membership already granted."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cut = _load('scripts/owner/apply-pg-cutover.py', 'cutover_gate')
reb = _load('scripts/owner/rebuild.py', 'rebuild_gate')


class Readiness:
    def __init__(self, missing=()):
        self.missing = set(missing)
    def role_inventory(self, conn, login):
        return [{'role': r, 'role_exists': True, 'status': 'missing' if r in self.missing else 'present'} for r in ('r_a', 'r_b')]


def _asked(reviewed, readiness):
    asked = []
    def confirm(prompt):
        asked.append(prompt)
        return ''   # an owner who just presses Enter: cancels
    try:
        result = cut.apply(object(), reviewed, readiness, ROOT, 'delivery_app', confirm)
    except ValueError as error:
        return asked, str(error)
    return asked, result


def test_nothing_pending_and_roles_granted_asks_nothing(capsys):
    asked, result = _asked([{'file': 'a.sql', 'status': 'present', 'destructive': []}], Readiness())
    assert asked == [] and result == 0
    assert '0 migrations pending' in capsys.readouterr().out


def test_a_pending_migration_still_asks_and_names_the_count():
    asked, result = _asked([{'file': 'a.sql', 'status': 'pending', 'destructive': [], 'sha256': 'x'}], Readiness())
    assert len(asked) == 1 and 'APPLY 1 MIGRATIONS TO delivery_app' in asked[0]
    assert 'confirmation did not match' in result


def test_a_missing_role_membership_still_asks_even_with_no_migration():
    asked, result = _asked([{'file': 'a.sql', 'status': 'present', 'destructive': []}], Readiness(missing=['r_b']))
    assert len(asked) == 1 and 'APPLY 0 MIGRATIONS' in asked[0]


def test_partial_migration_still_refuses():
    with pytest.raises(ValueError, match='partial'):
        cut.apply(object(), [{'file': 'a.sql', 'status': 'partial', 'destructive': []}], Readiness(), ROOT, 'x', lambda p: '')


def test_work_to_apply_lists_what_is_left():
    files, missing = cut.work_to_apply(object(), [{'file': 'a', 'status': 'present'}, {'file': 'b', 'status': 'pending'},
                                                  {'file': 'c', 'status': 'partial'}], Readiness(missing=['r_a']), 'x')
    assert files == ['b', 'c'] and missing == ['r_a']


def _step_ctx(tmp_path, code):
    asked, ran = [], []
    base = {'req': tmp_path / 'req', 'state_path': tmp_path / 's.json', 'state': {'schema': 1, 'key': 'k', 'steps': {}},
            'typed': lambda p: asked.append(p) or 'SKIP', 'run_child': lambda a, **k: ran.append(a),
            'probe': lambda a: (ran.append(a), code)[1]}
    return base, asked, ran


def test_rebuild_does_not_ask_when_the_dry_review_says_nothing_pending(tmp_path):
    c, asked, ran = _step_ctx(tmp_path, 0)
    reb.step_migrate(c)
    assert asked == [] and len(ran) == 1 and ran[0][-1] == '--check'
    assert 'migrate-apply' not in c['state']['steps']


def test_rebuild_still_asks_when_something_is_pending(tmp_path):
    c, asked, ran = _step_ctx(tmp_path, 3)
    reb.step_migrate(c)
    assert len(asked) == 1 and 'APPLY MIGRATIONS' in asked[0]


def test_rebuild_stops_when_the_dry_review_itself_fails(tmp_path):
    c, asked, ran = _step_ctx(tmp_path, 1)
    with pytest.raises(reb.WorkflowError, match='dry review failed'):
        reb.step_migrate(c)
    assert asked == []


def test_req_owner_passes_check_only_to_migrate():
    text = (ROOT / 'scripts/ops/req-owner.sh').read_text()
    assert "--check) [ \"$command\" = migrate ]" in text and '--check' in text.split('migrate)\n   args=')[1].split('rebuild)')[0]
