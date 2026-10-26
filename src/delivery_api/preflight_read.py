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
