"""Artifact presentation/export has one persistent authority: owned PG bytes."""
import ast,inspect
import pytest
from delivery_api import app as module

@pytest.mark.parametrize('function',[module._task_artifact_snapshot,module.task_phase_output,
    module.task_artifact_download,module.task_artifact_view,module.task_artifacts_zip,
    module._session_bundle,module.sessions])
def test_no_filesystem_or_backend_fallback(function):
    forbidden={'hasattr','_task_artifact','read_text','read_bytes','read_session_bytes','authorized_session','session_reader','FileResponse','write','is_file','stat'}
    tree=ast.parse(inspect.getsource(function))
    assert not [node for node in ast.walk(tree) if isinstance(node,ast.Call) and
        ((isinstance(node.func,ast.Name) and node.func.id in forbidden) or
         (isinstance(node.func,ast.Attribute) and node.func.attr in forbidden))]

@pytest.mark.parametrize('function',[module._decision_provenance,module._validation_panel_for,
    module.task_classifications,module.task_execution,module.session_cost])
def test_supplemental_evidence_has_no_host_authority(function):
    source=inspect.getsource(function)
    for forbidden in ('read_text(', 'read_bytes(', '.is_file(', '.glob(', 'bundles.root(', "hasattr(store,'_postgres')"):
        assert forbidden not in source

def test_model_call_diagnostics_are_pure_retained_byte_projection():
    from delivery_api import task_diagnostics
    source=inspect.getsource(task_diagnostics.model_calls)
    assert '.is_file(' not in source and '.open(' not in source

@pytest.mark.parametrize('function',[module._reconcile_terminal_http_interview,module.retry_failed_needs,module.retry_failed_phase,module.task_audit,module._task_cost,module.task_cost])
def test_retry_and_cost_have_no_file_reads(function):
    source=inspect.getsource(function)
    for forbidden in ('read_text(', 'usage_tokens(root/', 'write_cost_json(', 'usage_evidence(root)'):
        assert forbidden not in source
