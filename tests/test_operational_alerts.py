"""No private content, no sender, no money-state changes from logging."""
import json,logging
import pytest
from test_attempt_ledger import expense_guard,credit_account
from delivery_api.operational_alerts import emit


def test_allowlisted_signal_is_structured_and_excludes_user_content(caplog):
    with caplog.at_level(logging.WARNING,logger='requi.alert'):
        emit('tripwire_breach','session_expense_limit')
        emit('provider_failure','secret body')
        emit('arbitrary','provider_usage')
    assert len(caplog.records)==1
    value=json.loads(caplog.records[0].message.removeprefix('REQUI_ALERT '))
    assert value==dict(version=1,event='tripwire_breach',reason='session_expense_limit',action='operator_review')


def test_failed_log_sink_cannot_break_accounting(monkeypatch):
    def fail(*a,**kw):raise OSError('offline')
    monkeypatch.setattr(logging.getLogger('requi.alert'),'warning',fail)
    emit('unresolved_accounting','compute_usage')


def test_actual_guard_and_unknown_usage_emit_safe_signal(expense_guard,caplog):
    from test_attempt_ledger import A,LAUNCH_Q,LAUNCH_USAGE
    from delivery_api.tenancy import tenant_context
    import uuid
    s,tid,job=expense_guard
    with tenant_context(A),caplog.at_level(logging.WARNING,logger='requi.alert'):
        aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,LAUNCH_Q,'unknown-usage')
        assert s.start_launch_provider_attempt(aid)['started']
        s.transition_provider_attempt(aid,'settle',None)
        assert s.confirmed_expense_admission(job)=='unknown_provider_outcome'
    values=[json.loads(r.message.removeprefix('REQUI_ALERT ')) for r in caplog.records if r.name=='requi.alert']
    assert {'provider_usage','unknown_provider_outcome'}<={v['reason'] for v in values}
    assert all(set(v)=={'version','event','reason','action'} for v in values)


def test_confirmed_breach_signal_does_not_cancel_inflight_settlement(expense_guard,caplog):
    from test_attempt_ledger import A,LAUNCH_Q,LAUNCH_USAGE,DSN
    from delivery_api.tenancy import tenant_context
    import psycopg,uuid
    s,tid,job=expense_guard
    with psycopg.connect(DSN) as c:c.execute('UPDATE expense_guard_policy SET session_units=1,per_registered_user_units=1000000')
    with tenant_context(A),caplog.at_level(logging.WARNING,logger='requi.alert'):
        aid=uuid.uuid4().hex;s.reserve_provider_attempt(job,aid,LAUNCH_Q,'confirmed')
        assert s.start_launch_provider_attempt(aid)['started']
        s.transition_provider_attempt(aid,'settle',LAUNCH_USAGE)
        assert s.provider_attempt(aid)['state']=='settled'
        assert s.confirmed_expense_admission(job)=='session_expense_limit'
    values=[json.loads(r.message.removeprefix('REQUI_ALERT ')) for r in caplog.records if r.name=='requi.alert']
    assert any(v['event']=='tripwire_breach' and v['reason']=='session_expense_limit' for v in values)


def test_shipping_filter_only_allows_exact_signal_schema():
    from pathlib import Path
    import re
    config=(Path(__file__).parents[1]/'scripts/ops/grafana/config.alloy').read_text()
    expression=json.loads(next(line.split(' = ',1)[1] for line in config.splitlines() if 'expression =' in line))
    good='REQUI_ALERT '+json.dumps(dict(action='operator_review',event='provider_failure',reason='provider_outcome_incomplete',version=1),sort_keys=True,separators=(',',':'))
    assert re.search(expression,good).group('payload')==good.removeprefix('REQUI_ALERT ')
    for bad in ['Private user transcript',good.replace('operator_review','secret'),good[:-1]+',"private":"data"}',good+' trailing content']:
        assert re.search(expression,bad) is None
    assert 'action = "drop"' in config and 'password_file = sys.env("REQUI_LOKI_TOKEN_FILE")' in config


def test_provider_failure_signal_is_emitted_without_private_exception(caplog):
    import httpx
    from test_child_provider_broker import setup,request,BrokerPaused
    def fail(req):raise httpx.ReadTimeout('private provider payload')
    broker,gateway,_,calls=setup(fail)
    with caplog.at_level(logging.WARNING,logger='requi.alert'),pytest.raises(BrokerPaused):broker.dispatch(request())
    assert gateway.calls[-1]=='uncertain' and len(calls)==1
    lines=[r.message for r in caplog.records if r.name=='requi.alert']
    assert any('provider_failure' in line for line in lines) and all('private' not in line for line in lines)
