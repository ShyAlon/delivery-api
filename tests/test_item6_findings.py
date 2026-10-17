import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import json,os
import findings


def test_record_appends_json_lines_with_iso_time_owner_only(tmp_path):
    p=tmp_path/'f.jsonl'
    assert findings.record('a','one',p) and findings.record('b','two',p)
    rows=[json.loads(l) for l in p.read_text().splitlines()]
    assert [r['kind'] for r in rows]==['a','b'] and rows[0]['time'].startswith('2026') or rows[0]['time'][:2]=='20'
    assert p.stat().st_mode&0o077==0


def test_a_missing_directory_or_unwritable_file_never_raises(tmp_path):
    assert findings.record('a','x',tmp_path/'nope'/'f.jsonl') is False
    d=tmp_path/'dir';d.mkdir()
    assert findings.record('a','x',d) is False


def test_text_is_bounded(tmp_path):
    p=tmp_path/'f.jsonl';findings.record('k'*100,'t'*5000,p)
    r=json.loads(p.read_text());assert len(r['text'])==1000 and len(r['kind'])==60
