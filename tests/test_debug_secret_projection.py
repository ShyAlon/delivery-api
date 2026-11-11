def test_prefixed_and_quoted_secret_assignments_are_redacted_without_environment():
    from delivery_api.debug_export import redact
    for key in ('INTERVIEW_ENGINE_TOKEN','some_secret_value','X-Auth-Token','clientCredential','PASSWORD_HASH'):
        for template in ('{}=FAKE-NO-EXPORT-1234','"{}": "FAKE-NO-EXPORT-1234"',"{}='FAKE-NO-EXPORT-1234'"):
            assert 'FAKE-NO-EXPORT-1234' not in redact(template.format(key))


def test_server_and_browser_free_text_key_redaction_matches():
    from delivery_api.log_safety import safe_text
    import subprocess,json
    from pathlib import Path
    text='INTERVIEW_ENGINE_TOKEN=FAKE-ENGINE-1234 X-Secret-Value="FAKE SECRET WITH SPACES" clientCredential=FAKE-CREDENTIAL-1234'
    assert 'FAKE' not in safe_text(text)
    path=Path(__file__).parents[1]/'src/delivery_api/web/log-scrub.js'
    run=subprocess.run(['node','--input-type=module','-e',f'import {{scrubText}} from {json.dumps(str(path))};console.log(scrubText({json.dumps(text)}))'],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert 'FAKE' not in run.stdout
