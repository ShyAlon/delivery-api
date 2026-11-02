"""Customer assets cannot refer to private operations pages or vocabulary."""
from pathlib import Path
import re

ROOT=Path(__file__).parents[1]/'src/delivery_api/web'

def test_all_customer_assets_have_no_private_operations_vocabulary():
    forbidden=re.compile(r'back[ -]?office|model[ -]?pins|money gateway',re.I)
    for path in ROOT.rglob('*'):
        if path.suffix in ('.html','.js','.css'):
            assert not forbidden.search(path.read_text()),path.name


def test_shared_loading_status_has_motion_and_reduced_motion_fallback():
    css=(ROOT/'style.css').read_text()
    assert '.loading::before' in css and '@keyframes workspace-loading-spin' in css
    assert 'animation:workspace-loading-spin .8s linear infinite' in css
    assert '@media(prefers-reduced-motion:reduce){.loading::before{animation:none' in css
