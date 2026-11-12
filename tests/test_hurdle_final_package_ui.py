"""Final package button through the real browser, PG and zero-model producer."""
import hashlib,json,zipfile,os
from pathlib import Path
from test_cloud_final_package import seed,export_env
from delivery_api.tenancy import tenant_context


def test_final_package_button_downloads_verified_zip(pg_browser,export_env,tmp_path):
    page,base,workspace=pg_browser
    tid,_=seed(workspace)
    with workspace.admin() as connection:
        connection.execute("UPDATE tasks SET state='done' WHERE task_id=%s",(tid,))
    page.goto(base+'/tasks/'+tid+'/complete')
    page.locator('#prepare-package').click()
    link=page.get_by_role('link',name='Download coding-agent package',exact=True)
    with page.expect_download(timeout=30000) as pending:link.click()
    destination=tmp_path/'final-package.zip';pending.value.save_as(str(destination))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('package.json'))
        assert manifest['task_id']==tid and manifest['source']['owner_id']==workspace.scope.owner_id
        assert 'spec/AGENTS.md' in archive.namelist()
        assert 'CODING_AGENT_PROMPT.md' in archive.namelist()
        assert 'Implement the product' in archive.read('CODING_AGENT_PROMPT.md').decode()
        for name,digest in manifest['sha256'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==digest
    page.get_by_text('Package contents',exact=True).click()
    assert page.get_by_text('CODING_AGENT_PROMPT.md',exact=True).is_visible()
    evidence=Path(os.environ.get('REQUI_TEST_ARTIFACTS',tmp_path));evidence.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(evidence/'final-package-ui.png'),full_page=False)
