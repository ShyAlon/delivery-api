"""Preview preflight now belongs to typed settings/receipt/runtime APIs.

The removed shell launcher collected provider keys, chose behavior modes, applied
live schema, rebuilt and published mutable tags. Those are no longer preview
responsibilities. Equivalent safety assertions are executable in:
- test_image_settings: private mode, required DB/Auth/token, malformed/duplicate
  values and secret-as-literal handling, no private value echo or write.
- test_local_image_preview: exact current acceptance, dry review/decline no run,
  real browser failure retains container and named evidence.
- test_exact_image_build: dirty/source drift/secret exclusion and amd64 identity.
- test_image_workflow: missing tools/checkouts, strict explicit goal, stage
  progress, no mutation on decline, resumable evidence not saved booleans.
- test_image_private_backoffice and test_local_image_run: owned listeners and
  cleanup, same unchanged-image execution spec, no unrelated shutdown.
Runtime admission/model-pin tests still own reviewed route and money checks.
"""
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_preview_alias_preserves_explicit_settings_and_no_provider_collection(tmp_path):
    ops=tmp_path/'ops';ops.mkdir();script=ops/'preview-exec.sh'
    script.write_bytes((ROOT/'scripts/ops/preview-exec.sh').read_bytes())
    output=tmp_path/'args'
    (ops/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    result=subprocess.run(['bash',str(script),'--apply'],env=dict(os.environ,OUTPUT=str(output),REQUI_PREVIEW_ENV='/private space/preview.env',PREVIEW_EXEC_PORT='8011'),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text().splitlines()==['--preview','--name','requi-preview-exec','--port','8011','--env-file','/private space/preview.env','--apply']
    assert not result.stdout+result.stderr
    assert 'DEEPSEEK_API_KEY' not in script.read_text()


def test_preview_has_no_live_schema_apply_price_scraper_or_alternate_build():
    source=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    for forbidden in ('schema-readiness.py --offer-apply','pin-local.py','paid-pin','docker build','ssh ', 'build_exec_env', 'PREVIEW_EXEC_PROVIDER'):
        assert forbidden not in source
    assert 'image.sh' in source and '--preview' in source
