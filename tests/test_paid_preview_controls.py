"""Explicit preview opt-in is not a production/cloud default."""
import pytest
from delivery_api.paid_preview import construct,require_preview
from delivery_api.child_pool_budget import child_mode,pool_budget
from types import SimpleNamespace


def settings(**kw):
    return dict(DELIVERY_CHILD_EXECUTION='attended-paid',DELIVERY_PAID_CHILD_PREVIEW='1',DELIVERY_PREVIEW='1',DELIVERY_PUBLIC_ORIGIN='http://127.0.0.1:8090',DEEPSEEK_API_KEY='fixture-only',**kw)


def test_preview_constructor_uses_exact_reviewed_models_without_calling_provider():
    env=settings();models={'deepseek':'deepseek-flash'}
    runtime=construct(env,SimpleNamespace(models=models))
    assert runtime.models==models and child_mode(env) and pool_budget(env)==(8,4,0)
    assert construct({},None) is None


@pytest.mark.parametrize('key,value',[('DELIVERY_PAID_CHILD_PREVIEW','0'),('DELIVERY_PREVIEW','0'),('DELIVERY_PUBLIC_ORIGIN','https://requi.re'),('DELIVERY_PUBLIC_ORIGIN','http://127.0.0.1.evil.test'),('DELIVERY_PUBLIC_ORIGIN','http://user@localhost'),('DELIVERY_CONTROL_PLANE_ONLY','1')])
def test_paid_preview_requires_all_explicit_local_boundaries(key,value):
    env=settings();env[key]=value
    with pytest.raises(ValueError):construct(env,SimpleNamespace(models={'deepseek':'x'}))
    with pytest.raises(ValueError):child_mode(env)


def test_pure_local_mode_never_constructs_a_paid_runtime():
    env=settings();env['DELIVERY_CHILD_EXECUTION']='local'
    assert construct(env,None) is None


def test_launcher_drops_inherited_paid_selectors_and_requires_private_operator_policy():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/ops/preview-exec.sh').read_text()
    build=text.split('build_exec_env(){',1)[1].split("cat <<'PLAN'",1)[0]
    assert 'DELIVERY_CHILD_EXECUTION|DELIVERY_PAID_CHILD_PREVIEW|' in build
    gate=text.index('paid dispatch requires an audited Back office expense policy')
    assert gate<text.index('DELIVERY_CHILD_EXECUTION=attended-paid',gate)<text.index('docker run -d')
    assert 'explicit USD ceiling confirmation' not in text
