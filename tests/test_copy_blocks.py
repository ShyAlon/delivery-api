"""Hover Copy on every dark text block, and the Debug dialog layout."""
import re
from pathlib import Path

WEB=Path(__file__).resolve().parents[1]/'src'/'delivery_api'/'web'


def _web(name):return (WEB/name).read_text()


def test_the_copy_module_covers_every_dark_block_class_and_gives_busy_and_result_feedback():
    js=_web('copy-blocks.js')
    for selector in ('.json-view','.console','.live-console','.artifact-content','.input-requirement code'):assert selector in js.split('export function')[0]
    assert "import {deliverText} from './client-core.js'" in js and 'export function startCopyBlocks' in js
    for label in ("'Copy'","'Copying…'","'Copied'","'Downloaded'","'Copy failed'"):assert label in js
    assert "aria-live" in js and 'if(busy||!target)return' in js and 'button.disabled=true' in js


def test_one_floating_button_is_reused_so_no_block_is_wrapped_or_moved():
    js=_web('copy-blocks.js')
    assert js.count("document.createElement('button')")==1 and 'wrap' not in js.lower().replace('wrapped or re-laid','')
    assert "addEventListener('pointerover'" in js and 'getBoundingClientRect' in js and "capture:true" in js
    css=_web('style.css')
    assert re.search(r'\.copy-block\{position:fixed;z-index:60',css) and '.copy-block.hidden{display:none}' in css


def test_the_app_starts_the_copy_module_once_and_preloads_it():
    app=_web('app.js')
    assert "import {startCopyBlocks} from './copy-blocks.js'" in app and app.count('startCopyBlocks()')==1
    assert '<link rel="modulepreload" href="/assets/copy-blocks.js">' in _web('index.html')


def test_dark_blocks_in_the_stylesheet_are_all_copyable():
    css=_web('style.css');js=_web('copy-blocks.js')
    covered=re.search(r"COPY_BLOCKS='([^']+)'",js).group(1).split(',')
    dark=set(re.findall(r'(\.(?:json-view|console|live-console|artifact-content))\{[^{}]*background:#1[0-9a-f]{5}',css))
    assert dark and all(any(c==d or c.endswith(d) for c in covered) for d in dark)


def test_debug_dialog_buttons_sit_in_one_aligned_row_with_gaps():
    s=_web('identity.js')
    row=s.split('<div class="debug-actions">')[1].split('</div>')[0]
    assert row.count('<button')==4 and 'id="downloaddebug"' in row and 'id="copyclientlogs"' in row
    css=_web('style.css')
    assert re.search(r'\.debug-dialog \.debug-actions\{display:flex;flex-wrap:wrap;gap:12px',css)
    assert re.search(r'\.debug-dialog \.debug-actions button\{margin:0;padding:12px 18px;min-height:44px',css)


def test_debug_dialog_sections_are_separated_ledger_first_and_open_and_the_close_button_stays_reachable():
    s=_web('identity.js');css=_web('style.css')
    dialog=s.split('class="dialog debug-dialog"')[1].split('</div>`,{focus')[0]
    assert dialog.index('id="ledgerwrap" open')<dialog.index('Current technical state')<dialog.index('Recent requests')
    assert '<details open><summary>Recent requests' not in dialog
    assert '.debug-dialog details{margin:0;padding:12px 0;border-top:1px solid var(--line)}' in css
    assert re.search(r'\.debug-dialog \.dialog-x\{position:sticky;top:0;float:right',css)


def test_boxes_inside_the_debug_dialog_do_not_scroll_inside_the_scrolling_dialog():
    css=_web('style.css')
    assert re.search(r'\.debug-dialog \.json-view\{max-height:none;margin:0;overflow-x:auto;overflow-y:visible\}',css)
