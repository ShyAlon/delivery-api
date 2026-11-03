"""Inherited Dialog/Page debt is explicit; new bypasses fail the ratchet."""
from pathlib import Path
import runpy,json,shutil
ROOT=Path(__file__).parents[1]

def module():return runpy.run_path(str(ROOT/'scripts/ci/check-ui-boundaries.py'))

def test_components_have_zero_table_exceptions_and_no_debt_expansion():
    m=module();baseline=json.loads((ROOT/'docs/ui-component-debt.json').read_text())
    assert not [key for key in baseline if key.startswith(('table:','table-style:'))]
    assert not m['check'](ROOT/'src/delivery_api',baseline)

def test_page_owned_table_dialog_and_shell_all_fail(tmp_path):
    m=module();(tmp_path/'web').mkdir();(tmp_path/'backoffice_web').mkdir()
    page=tmp_path/'web/new-page.js'
    for markup,kind in [('<table><tr><td>x</td></tr></table>','table:'),('<dialog>x</dialog>','dialog:'),('<main>x</main>','page:')]:
        page.write_text('const html=`'+markup+'`')
        assert any(error.startswith(kind) for error in m['check'](tmp_path,{}))
