"""Engine child timing records: retained when structured, withheld when arbitrary."""
import asyncio
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault('DELIVERY_DATABASE_URL','postgresql://invalid/none')
os.environ.setdefault('DELIVERY_SUPABASE_URL','https://invalid.example')
os.environ.setdefault('DELIVERY_SUPABASE_ANON_KEY','test-anon-key')

from delivery_api.child_supervisor import ChildSupervisor
from delivery_api.child_execution_contract import decode_operation
from test_child_execution_contract import operation,frame

FIXTURE=Path(__file__).parent/'fixtures'/'child_worker_fixture.py'
SESSION='12345678-1234-1234-1234-123456789012'


def test_child_entry_routes_engine_timings_to_stderr(capsys):
    from delivery_api import child_entry
    child_entry.install_engine_timing_log()
    logging.getLogger('interview_engine.application').info(
        'advance.turn.end session=%s dur_ms=%d',SESSION,42)
    assert f'advance.turn.end session={SESSION} dur_ms=42' in capsys.readouterr().err


def test_structured_timings_retained_and_arbitrary_stderr_withheld(tmp_path,monkeypatch):
    from delivery_api import config,process_logs
    monkeypatch.setattr(config,'default_data_dir',lambda:tmp_path)
    s=ChildSupervisor([sys.executable,str(FIXTURE),'timings'],grace_seconds=.1,kill_seconds=.1)
    result=asyncio.run(s.run(decode_operation(frame(operation())),authorize=lambda op:None,on_event=lambda e:None))
    assert result.returncode==0
    assert process_logs.flush()
    texts=[row['text'] for row in process_logs.selected().records(stream='engine',limit=100)['records']]
    assert any(t==f'advance.turn.begin session={SESSION}' for t in texts)
    assert any(t==f'advance.turn.end session={SESSION} dur_ms=17123' for t in texts)
    assert not any('must never be retained' in t or 'Traceback' in t for t in texts)
    assert all(row['session_id']==SESSION for row in process_logs.selected().records(stream='engine',limit=100)['records'])
