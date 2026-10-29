"""Owner-approved local rerun contracts must not impersonate tenant coverage."""
import ast
from pathlib import Path

RERUN_CASES = {
    'test_rerun_preview_explains_archive_and_audit_has_local_actor',
    'test_confirm_rerun_button_disables_until_the_server_returns',
    'test_rerun_review_opens_top_dialog_and_escape_restores_flow',
}


def test_rerun_browser_cases_live_only_in_local_mode_suite():
    root = Path(__file__).parent
    cloud = ast.parse((root / 'test_ui_playwright.py').read_text())
    cloud_names = {n.name for n in cloud.body if isinstance(n, ast.FunctionDef)}
    assert not (RERUN_CASES & cloud_names), 'local rerun cases still in shared cloud suite'
    local = ast.parse((root / 'test_local_rerun_browser.py').read_text())
    cases = {n.name: n for n in local.body if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}
    assert set(cases) == RERUN_CASES
    for case in cases.values():
        assert [arg.arg for arg in case.args.args] == ['page', 'ui']
