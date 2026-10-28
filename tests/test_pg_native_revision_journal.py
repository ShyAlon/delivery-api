"""Revision SQL ownership, without changing or enabling installation policy."""
import ast
import inspect
import pytest
from delivery_api.tenancy import tenant_context

REVISION_METHODS=('installed_execution_anchor','retain_closed_revision_restart_intent',
 'retain_closed_revision_lineage_plan','append_closed_revision_preparation_event',
 'reconcile_private_revision_installation','read_promoted_installation_evidence',
 'activate_private_revision_installation','private_approved_continuation_basis',
 'prepare_private_revision_changes','record_private_revision_acceptance',
 'verify_private_revision_acceptance','revoke_private_revision_acceptance',
 'retain_private_installation_intent','append_private_installation_event',
 'inspect_revision_install_safety','retain_revision_installation_review',
 'prepare_revision_staging','record_validated_revision_staging','retain_revision_source_review')


def test_all_revision_method_queries_are_native():
    from delivery_api.postgres_store import PostgresStore
    import textwrap
    for name in REVISION_METHODS:
        tree=ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(getattr(PostgresStore,name)))))
        assert not any(isinstance(n,ast.Attribute) and n.attr=='_db' for n in ast.walk(tree)),name


def test_native_held_lineage_and_journal_runtime(pg_workspace_factory,monkeypatch):
    from delivery_api.postgres_store import _Connection
    from test_postgres_store import _held_lineage_fixture
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store;original=_Connection.execute
        def reject(self,sql,parameters=()):
            if 'closed_revision_' in sql or 'installed_execution_bindings' in sql:
                raise AssertionError('revision journal still consumes dialect bridge')
            return original(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            tid,args=_held_lineage_fixture(s);before=s.task(tid)
            plan=s.retain_closed_revision_lineage_plan(tid,**args);lid=plan['lineage_id']
            assert s.retain_closed_revision_lineage_plan(tid,**args)['replayed']
            first=s.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,state='prepared',evidence={'intent_id':args['intent_id'],'lineage_id':lid})
            evidence={'manifest_sha256':'f'*64,'checkpoint_id':'staged-checkpoint'}
            staged=s.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],state='staged',evidence=evidence,read_staged_receipt=lambda:evidence)
            with pytest.raises(ValueError,match='compare-and-swap'):
                s.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=first['event_sha256'],state='held_for_review',evidence={'reason':'interrupted'})
            assert not staged['event']['dispatch_started']
            assert s.task(tid)==before
            assert s.session_binding(args['new_execution_session_id']) is None
            assert s.installed_execution_anchor(args['new_execution_session_id']) is None
