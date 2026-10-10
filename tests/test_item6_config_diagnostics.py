"""Found/expected/where without secrets, arbitrary values or file mutations."""
import json,sys,subprocess
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from scaffold import scaffold
from config_diagnostics import diagnose


def test_original_empty_scaffold_can_import(tmp_path):
 root=tmp_path/'run';scaffold(root)
 assert diagnose(root)['import_ready'] is True


def test_decimal_vendor_prices_explain_fixed_point_without_printing_values(tmp_path):
 root=tmp_path/'run';scaffold(root);p=root/'private/catalog.json';d=json.loads(p.read_text());d[0]['expense_output']=0.6;p.write_text(json.dumps(d))
 r=diagnose(root);assert r['import_ready'] is False
 assert any(c['where'].endswith('expense_output') and 'integer' in c['expected'] and 'float' in c['found'] for c in r['checks'])
 assert '0.6' not in json.dumps(r)


def test_invalid_credential_json_suppresses_content_and_gives_line_column(tmp_path):
 root=tmp_path/'run';scaffold(root);p=root/'private/credentials.json';p.write_text('{"client_secret": "secret_marker\n}')
 r=diagnose(root);body=json.dumps(r);assert 'secret_marker' not in body and 'line' in body and 'column' in body
 assert any(c['where']=='private/credentials.json' for c in r['checks'])


def test_missing_field_existing_env_and_conflicts_are_batched(tmp_path):
 root=tmp_path/'run';scaffold(root)
 (root/'private/.env').write_text('secret_marker')
 p=root/'private/run.json';d=json.loads(p.read_text());d['model']='secret_marker';d.pop('catalog_file');d['database_name']='secret_marker';p.write_text(json.dumps(d))
 r=diagnose(root);body=json.dumps(r)
 assert not r['import_ready'] and len(r['checks'])>=4
 assert 'secret_marker' not in body
 assert all({'checked','expected','found','where'}<=set(c) for c in r['checks'])


def test_diagnostic_and_failed_import_do_not_write_source(tmp_path):
 root=tmp_path/'run';scaffold(root);p=root/'private/catalog.json';p.write_text('{not valid')
 before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
 cli=Path(__file__).parents[1]/'scripts/item6/scaffold.py'
 result=subprocess.run([sys.executable,str(cli),'import-config',str(root)],capture_output=True,text=True)
 assert result.returncode==1 and 'JSON syntax' in result.stdout and not result.stderr
 after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
 assert before==after


def test_symlink_parent_never_parses_external_file(tmp_path):
 root=tmp_path/'run';scaffold(root);real=root/'private';target=tmp_path/'moved';real.rename(target);real.symlink_to(target,target_is_directory=True)
 r=diagnose(root)
 assert not r['import_ready'] and any('symlink' in c['found'] for c in r['checks'])
