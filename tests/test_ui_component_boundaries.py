"""Structural components have no exceptions, including legacy allowances."""
from pathlib import Path
import runpy,json,shutil
ROOT=Path(__file__).parents[1]

def module():return runpy.run_path(str(ROOT/'scripts/ci/check-ui-boundaries.py'))

def test_components_have_zero_table_exceptions_and_no_debt_expansion():
    m=module();baseline=json.loads((ROOT/'docs/ui-component-debt.json').read_text())
    assert not [key for key in baseline if key.startswith(('table:','table-style:','dialog:','dialog-style:'))]
    assert not m['check'](ROOT/'src/delivery_api',baseline)

def test_page_owned_table_dialog_and_shell_all_fail(tmp_path):
    m=module();(tmp_path/'web').mkdir();(tmp_path/'backoffice_web').mkdir()
    page=tmp_path/'web/new-page.js'
    for markup,kind in [('<table><tr><td>x</td></tr></table>','table:'),('<dialog>x</dialog>','dialog:'),('<main>x</main>','page:')]:
        page.write_text('const html=`'+markup+'`')
        assert any(error.startswith(kind) for error in m['check'](tmp_path,{}))


def test_dialog_styles_and_native_construction_cannot_escape_component(tmp_path):
    m=module();(tmp_path/'web').mkdir();(tmp_path/'backoffice_web').mkdir()
    for markup in ('.dialog{width:10px}', '.shared-dialog{overflow:hidden}'):
        (tmp_path/'web/new.css').write_text(markup)
        assert any(error.startswith('dialog-style:') for error in m['check'](tmp_path,{}))


def test_zero_exception_gate_rejects_allowances_programmatic_and_nested_bypasses(tmp_path):
    m=module();folder=tmp_path/'web/features';folder.mkdir(parents=True);(tmp_path/'backoffice_web').mkdir()
    path=folder/'new.js'
    cases=[
        ("document.createElement ( 'main' )",'page:'),
        ('document.createElementNS(null, "dialog")','dialog:'),
        ('h("table", {})','table:'),
        ('node.setAttribute("role", "alertdialog")','dialog:'),
        ('node.classList.add("page-head")','page:'),
        ('node.className="operator-intro"','page:'),
        ('const css="header{position:fixed}"','page-style:'),
        ('const css=`.page-head{width:10px}`','page-style:'),
        ('style.textContent=".shared-dialog{width:10px}"','dialog-style:'),
        ('style.textContent=".ui-table td{display:none}"','table-style:'),
    ]
    for source,kind in cases:
        path.write_text(source)
        assert any(e.startswith(kind) for e in m['check'](tmp_path,{})),source
    path.unlink()
    assert m['check'](tmp_path,{'page:any.js':999}), 'legacy exceptions cannot authorize bypasses'


def test_gate_is_in_ci_and_vendor_only_scope_is_explicit():
    assert 'python scripts/ci/check-ui-boundaries.py' in (ROOT/'.github/workflows/ci-cd.yml').read_text()
    assert json.loads((ROOT/'docs/ui-component-debt.json').read_text())=={}


def test_owner_exemption_is_per_component_not_per_file(tmp_path):
    m=module();folder=tmp_path/'web';folder.mkdir();(tmp_path/'backoffice_web').mkdir()
    path=folder/'page-component.js';path.write_text("document.createElement('table')")
    assert any(e.startswith('table:') for e in m['check'](tmp_path))
    path.write_text("document.createElement('main')")
    assert not m['check'](tmp_path)


def test_legitimate_body_content_and_queries_are_not_construction(tmp_path):
    m=module();folder=tmp_path/'web';folder.mkdir();(tmp_path/'backoffice_web').mkdir()
    (folder/'content.js').write_text("""const body='<p class="dialog-intro">Review</p><div class="ui-table-scroll"></div>';
    const main=document.querySelector('main');if(table){sort(table)}
    function header(title){return title} // no structural CSS
    """)
    assert not m['check'](tmp_path)


def test_injected_css_and_nested_inline_style_cannot_bypass(tmp_path):
    m=module();folder=tmp_path/'backoffice_web/nested';folder.mkdir(parents=True);(tmp_path/'web').mkdir()
    for name,text,kind in [('view.html','<style>@media(max-width:600px){main{padding:0}}</style>','page-style:'),('view.js','const css=".shared-dialog{overflow:hidden}"','dialog-style:')]:
        path=folder/name;path.write_text(text)
        assert any(e.startswith(kind) for e in m['check'](tmp_path))
        path.unlink()
