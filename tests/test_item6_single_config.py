"""One human-edit source, explicit review, old-image generation; never Docker."""
import json,sys,subprocess
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from scaffold import scaffold
from single_config import create_single,import_legacy,load_env,write_env,review,compile_config,apply_proposal
from test_item6_scaffold import fill
from model_data import propose,PRICING_URL
from vendor_config import generate_snapshot
from test_item6_model_data import P,A


def filled(tmp_path):
 root=tmp_path/'run';scaffold(root);fill(root)
 # Owner's chosen model, existing price/context sources are fixture values.
 for name in ('run.json','catalog.json','context.json'):
  p=root/'private'/name;p.write_text(p.read_text().replace('fixture-model','deepseek-flash'))
 import_legacy(root);d,_=load_env(root);d['PRICING_BAND']='off_peak'
 p=root/'private/.env';p.unlink();write_env(p,d)
 generate_snapshot(root,'deepseek-flash',lambda url:P if url==PRICING_URL else A)
 return root


def test_import_once_preserves_credentials_and_rejects_conflicting_values(tmp_path):
 root=filled(tmp_path);d,_=load_env(root)
 assert d['DEEPSEEK_API_KEY']=='fixture-key'
 assert d['MODEL']=='deepseek-flash'
 with pytest.raises(FileExistsError):import_legacy(root)
 (root/'private/.env').unlink();p=root/'private/run.json';d=json.loads(p.read_text());d['database_name']='requi_item6_other_database';p.write_text(json.dumps(d))
 with pytest.raises(ValueError):import_legacy(root)
 assert not (root/'private/.env').exists()


def test_review_exact_hash_compile_private_snapshot_no_secret_output(tmp_path):
 root=filled(tmp_path);r=review(root)
 assert 'fixture-key' not in json.dumps(r) and 'fixture-secret' not in json.dumps(r)
 with pytest.raises(ValueError):compile_config(root)
 with pytest.raises(ValueError):review(root,'wrong')
 review(root,r['review_hash']);compiled=compile_config(root)
 runtime=Path(compiled['generated_runtime'])
 assert runtime.stat().st_mode&0o777==0o700
 for p in runtime.iterdir():assert p.stat().st_mode&0o777==0o600
 catalog=json.loads((runtime/'catalog.json').read_text());context=json.loads((runtime/'context.json').read_text())
 assert catalog[0]['price_version'].startswith('vendor-') and context['bound_version']=='owner-run-128000-v1'
 script=Path(compiled['dry_launcher']).read_text()
 assert '.env,dst=' not in script and '--go' in script and '--rm' not in script
 verifier=next((root/'private').glob('verify-*.py'))
 subprocess.run(['python3',str(verifier),str(root)],check=True)
 (root/'private/.env').write_text((root/'private/.env').read_text()+'# edit invalidates review\n')
 assert subprocess.run(['python3',str(verifier),str(root)],capture_output=True).returncode!=0
 with pytest.raises(ValueError):compile_config(root)


def test_generated_tamper_fails_and_partial_evidence_stops_compile(tmp_path):
 root=filled(tmp_path);r=review(root);review(root,r['review_hash']);c=compile_config(root)
 runtime=Path(c['generated_runtime']);p=runtime/'run.json';p.write_text('{}')
 verifier=next((root/'private').glob('verify-*.py'))
 assert subprocess.run(['python3',str(verifier),str(root)],capture_output=True).returncode!=0
 (root/'evidence/partial').write_text('stop')
 with pytest.raises(ValueError):compile_config(root)


def test_new_single_source_and_proposal_keep_review_manual(tmp_path):
 root=tmp_path/'new';create_single(root)
 assert not (root/'private/run.json').exists()
 before=(root/'private/.env').read_bytes()
 propose(root,'deepseek-flash',lambda url:P if url==PRICING_URL else A)
 with pytest.raises(ValueError,match='retired'):apply_proposal(root,'off_peak',1000000)
 assert (root/'private/.env').read_bytes()==before
 with pytest.raises(ValueError):review(root)


def test_duplicate_or_shell_expressions_never_evaluated(tmp_path):
 root=tmp_path/'new';create_single(root);p=root/'private/.env'
 p.write_text(p.read_text()+'MODEL="other"\n')
 with pytest.raises(ValueError):load_env(root)
 p.write_text('MODEL=$(touch /tmp/should-never-execute)\n')
 with pytest.raises(ValueError):load_env(root)


def test_apply_conflict_never_changes_existing_source(tmp_path):
 root=filled(tmp_path);before=(root/'private/.env').read_bytes()
 propose(root,'deepseek-flash',lambda url:P if url==PRICING_URL else A)
 with pytest.raises(ValueError):apply_proposal(root,'peak',1000000)
 assert (root/'private/.env').read_bytes()==before


def test_legacy_model_conflict_and_symlink_secrets_do_not_import(tmp_path):
 root=tmp_path/'run';scaffold(root);fill(root)
 with pytest.raises(ValueError):import_legacy(root)
 assert not (root/'private/.env').exists()
 for name in ('run.json','catalog.json','context.json'):
  p=root/'private'/name;p.write_text(p.read_text().replace('fixture-model','deepseek-flash'))
 p=root/'private/credentials.json';other=root/'copy';other.write_bytes(p.read_bytes());other.chmod(0o600);p.unlink();p.symlink_to(other)
 with pytest.raises(ValueError):import_legacy(root)


def test_generated_snapshot_roundtrips_rebuilt_schema_and_launcher_dry(tmp_path):
 root=filled(tmp_path);r=review(root);review(root,r['review_hash']);c=compile_config(root)
 runtime=Path(c['generated_runtime']);run=json.loads((runtime/'run.json').read_text())
 assert run['root']=='/evidence/run' and run['service_file']=='/private/services.conf'
 from isolation import parse_loopback_service
 assert parse_loopback_service((runtime/'services.conf').read_text(),'item6-payment')['database_name']==run['database_name']
 assert json.loads((runtime/'credentials.json').read_text())=={'client_id':'fixture-id','client_secret':'fixture-secret','deepseek_api_key':'fixture-key'}
 subprocess.run(['bash','-n',c['dry_launcher']],check=True)
 # A fake docker only records arguments, no network or container invocation.
 fake=tmp_path/'bin';fake.mkdir();docker=fake/'docker';docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGFILE"\n');docker.chmod(0o700)
 import os
 args=tmp_path/'args';env={**os.environ,'PATH':str(fake)+':'+os.environ['PATH'],'ARGFILE':str(args)}
 subprocess.run(['bash',c['dry_launcher']],env=env,check=True)
 lines=args.read_text().splitlines();assert '--go' not in lines
 assert any(str(runtime)+',dst=/private,readonly' in v for v in lines)
 assert not any('/private/.env,dst=' in v for v in lines)
