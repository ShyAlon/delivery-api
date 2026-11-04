"""Durable input authority must not be selected by a store attribute."""
import ast
import inspect
import pytest
from delivery_api import producer_inputs,revision_evidence,revision_proposal

@pytest.mark.parametrize('function',[producer_inputs.read_fresh_inputs,
    revision_evidence.read_revision_artifacts,revision_proposal.read_retained_revision_proposal])
def test_no_file_fallback_or_backend_selector(function):
    tree=ast.parse(inspect.getsource(function))
    forbidden={'open','fdopen','read_bytes','read_text','_open_directory','hasattr'}
    assert not [node for node in ast.walk(tree) if isinstance(node,ast.Call) and
        ((isinstance(node.func,ast.Name) and node.func.id in forbidden) or
         (isinstance(node.func,ast.Attribute) and node.func.attr in forbidden))]
