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
