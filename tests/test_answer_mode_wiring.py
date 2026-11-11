"""Default candidate and explicit legacy fallback; no provider execution."""
import pytest
from delivery_api.child_paid_runtime import PaidChildRuntime


def test_default_is_candidate_and_explicit_legacy_is_unchanged():
    from delivery_api.answer_mode import from_environment
    runtime=PaidChildRuntime(models={'deepseek':'reviewed-model'},api_key='fixture-only')
    assert str(from_environment({},runtime).start_credits)=='0.1'
    assert from_environment({'DELIVERY_ANSWER_ACCOUNTING_MODE':'legacy'},runtime) is None
    mode=from_environment({'DELIVERY_ANSWER_ACCOUNTING_MODE':'answer-v1'},runtime)
    assert str(mode.start_credits)=='0.1'


def test_typo_fails_closed_and_local_only_keeps_legacy():
    from delivery_api.answer_mode import from_environment
    with pytest.raises(ValueError,match='legacy or answer-v1'):
        from_environment({'DELIVERY_ANSWER_ACCOUNTING_MODE':'answer-v2'},None)
    assert from_environment({'DELIVERY_ANSWER_ACCOUNTING_MODE':'answer-v1'},None) is None


def owner_module():
    import importlib.util
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('owner_answer_mode',root/'scripts/owner/answer-mode.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m,root


@pytest.mark.parametrize('scenario',['dry','missing-schema','enable','disable','restart-failure'])
def test_optional_repair_settings_and_exact_restart(tmp_path,monkeypatch,scenario):
    import os
    m,root=owner_module();target=tmp_path/'private preview.env'
    target.write_text("DELIVERY_DATABASE_URL='postgresql:///fake'\nINTERVIEW_ENGINE_TOKEN='fixture-secret-not-real'\n")
    target.chmod(0o600);monkeypatch.setenv('REQUI_PREVIEW_ENV',str(target));monkeypatch.delenv('PREVIEW_SETTINGS_FILE',raising=False)
    checked=[];restarts=[]
    def check(dsn):
        checked.append(True)
        if scenario=='missing-schema':raise ValueError('migration missing')
    def restart(env):
        restarts.append(env)
        if scenario=='restart-failure':raise OSError('fake restart failed')
    mode='legacy' if scenario=='disable' else 'answer-v1'
    args=dict(check=check,restart=restart)
    if scenario in ('missing-schema','restart-failure'):
        with pytest.raises(ValueError):m.select(root,mode,True,**args)
    else:m.select(root,mode,scenario!='dry',**args)
    saved=scenario in ('enable','disable','restart-failure')
    values=m.settings_module(root).read_file(target)
    assert values['INTERVIEW_ENGINE_TOKEN']=='fixture-secret-not-real'
    assert (m.KEY in values)==saved
    assert bool(restarts)==saved
    if saved:
        assert values[m.KEY]==mode and restarts[0][m.KEY]==mode
        assert restarts[0]['REQUI_PREVIEW_ENV']==str(target)
        assert os.stat(target).st_mode & 0o077==0
    assert bool(checked)==(scenario not in ('dry','disable'))


def test_mode_setting_survives_shared_container_export(tmp_path,monkeypatch):
    m,root=owner_module();settings=m.settings_module(root)
    monkeypatch.setenv(m.KEY,'answer-v1');file=tmp_path/'container.env'
    settings.delivery_env(file)
    assert m.KEY+'=answer-v1' in file.read_text()


def test_owner_mode_is_first_class_in_both_req_entries():
    m,root=owner_module()
    assert 'answer-mode|locks|rebuild)' in (root/'scripts/ops/req').read_text()
    assert 'scripts/owner/answer-mode.py "$PWD" "$@"' in (root/'scripts/ops/req-owner.sh').read_text()


@pytest.mark.parametrize('ready',[True,False])
def test_runtime_schema_check_is_catalog_only(ready):
    from delivery_api.answer_mode import check_schema
    seen=[]
    class Connection:
        def execute(self,sql):seen.append(sql);return self
        def fetchone(self):return {'ready':ready}
    if ready:check_schema(Connection())
    else:
        with pytest.raises(ValueError,match='migration missing'):check_schema(Connection())
    assert len(seen)==1 and seen[0].startswith('SELECT ')


def test_candidate_install_failure_closes_gateway_and_installs_nothing(store,tmp_path,monkeypatch):
    from delivery_api import cloud_runtime
    from test_production_gateway import env,MODEL
    closed=[];chosen=[]
    class Factory:
        models={'deepseek':MODEL};verify_vendor=None
        def refresh_launch(self):pass
        def close(self):closed.append(True)
    cloud_runtime.uninstall()
    monkeypatch.setattr('delivery_api.model_pins.DurableGateways',lambda *a:Factory())
    def missing(conn):raise ValueError('answer receipt migration missing')
    monkeypatch.setattr('delivery_api.answer_mode.check_schema',missing)
    runtime=PaidChildRuntime(models=Factory.models,api_key='fixture')
    with pytest.raises(ValueError,match='migration missing'):
        cloud_runtime.install(store,env(DELIVERY_ANSWER_ACCOUNTING_MODE='answer-v1',DELIVERY_DATABASE_URL=__import__('test_postgres_store').DSN.replace('user=sandbox','user=delivery_app'),VMODEL_ARTIFACTS_DIR=str(tmp_path)),chosen.append,paid_runtime=runtime)
    assert closed==[True] and not chosen and not cloud_runtime.installed()
