"""Editable question-quality settings are local-mode-only by owner decision."""
import ast
from pathlib import Path


def test_question_quality_editor_is_only_in_local_suite():
    root=Path(__file__).parent
    cloud=ast.parse((root/'test_ui_playwright.py').read_text())
    functions={n.name:n for n in cloud.body if isinstance(n,ast.FunctionDef)}
    assert 'test_debug_dialog_edits_question_quality_settings' not in functions
    assert 'ui' not in functions and 'page' not in functions, 'unused local fixture bootstrap remains in shared suite'
    local=ast.parse((root/'test_local_question_quality_browser.py').read_text())
    cases=[n for n in local.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
    assert [n.name for n in cases]==['test_debug_dialog_edits_question_quality_settings']
    assert [n.arg for n in cases[0].args.args]==['page','ui']
