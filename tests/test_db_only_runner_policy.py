import inspect
from delivery_api.runner import ProcessRunner


def test_runner_requires_registered_child_and_retained_documents():
    source=inspect.getsource(ProcessRunner)
    assert 'asyncio.create_subprocess_exec(' not in source
    assert 'publish_committed_session' not in source
    assert 'pin_settled_artifacts' not in source
    assert 'price_execution' not in source
    assert 'path.read_text(' not in source
