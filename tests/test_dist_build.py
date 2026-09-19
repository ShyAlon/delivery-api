from pathlib import Path

def test_distribution_refuses_half_pulled_sibling_set_and_launcher_prints_shas():
    text=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    assert 'Resolved Requi.re source revisions:' in text
    assert 'HEAD..$upstream' in text and 'behind $upstream' in text
    assert 'This build would mix repository versions.' in text
    assert 'Installed Requi.re source revisions:' in text
    assert 'sed -n' in text and 'MANIFEST.txt' in text
