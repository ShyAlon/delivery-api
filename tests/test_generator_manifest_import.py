"""The private child cannot import the parent's environment-validated runtime."""
import os,subprocess,sys


def test_manifest_import_requires_no_service_credentials_or_parent_adapter():
    env={k:v for k,v in os.environ.items() if not k.startswith(('DELIVERY_','SUPABASE_'))}
    probe='from delivery_api.generator_manifest import manifest; import sys; assert "delivery_api.config" not in sys.modules; assert "delivery_api.child_generator" not in sys.modules; assert callable(manifest)'
    result=subprocess.run([sys.executable,'-c',probe],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
