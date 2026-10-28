"""Read the container's startup preflight report for the Debug dialog.

full_entry writes the report next to the engine log at container start; this
serves it back verbatim. A missing or unreadable report is a fact, not an
error: the container predates the preflight or wrote nothing."""
from __future__ import annotations

import json

from . import config


def report_path():
    """Mirrors scripts/docker/preflight.py: (XDG_DATA_HOME or ~/.local/share)/requi-re/logs."""
    return config.default_data_dir() / 'logs' / 'preflight-report.json'


def startup_report():
    try:
        path = report_path()
        if path.is_file():
            return json.loads(path.read_text(errors='replace'))
    except (OSError, ValueError):
        pass
    return None


def persistence_status():
    import os
    expected=os.environ.get('REQUI_PREFLIGHT_GENERATED_AT')
    saved=os.environ.get('REQUI_PREFLIGHT_REPORT_SAVED')
    report=startup_report()
    actual=report.get('generated_at') if isinstance(report,dict) else None
    return {'availability':'unavailable' if report is None else 'stale' if expected and actual!=expected else 'current' if expected else 'unverified',
            'write_status':'saved' if saved=='1' else 'failed' if saved=='0' else 'unknown',
            'expected_generated_at':expected,'cached_generated_at':actual,
            'authority':'startup stdout report'}
