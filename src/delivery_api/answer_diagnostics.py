"""Read-only, redacted export of local diagnostic recovery copies.

There is no execution replay or automatic document import in this module.
"""
import json
import os
from .debug_export import redact


def exported_json():
    from interview_engine.document_diagnostics import recovery_export
    from vmodel_contracts.runtime import artifacts_root
    return json.dumps(redact(recovery_export(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')))),ensure_ascii=False)
