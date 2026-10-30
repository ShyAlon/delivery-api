#!/usr/bin/env python3
"""Print only offline check booleans. Never dump environment values or connect."""
import json,os
if __name__=='__main__':
    import importlib.util
    from pathlib import Path
    _settings_path=Path(__file__).resolve().parent/'ops/settings.py'
    _spec=importlib.util.spec_from_file_location('ops_settings_entry',_settings_path)
    _settings=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_settings)
    try:_settings.apply_environment(Path(__file__).resolve().parents[1])
    except _settings.SettingsError as exc:raise SystemExit('SETTINGS ABORTED: '+str(exc))

from delivery_api.ops_preflight import preflight
if __name__=='__main__':
    result=preflight(os.environ)
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result['configuration_valid'] else 1)
