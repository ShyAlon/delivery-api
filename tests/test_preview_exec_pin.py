"""Hermetic equivalence: the shared ops pin step (scripts/ops/pin.py) behaves
exactly like the pin step that was embedded in go-live.sh before extraction.

Both run against the same captured public vendor pages (tests/fixtures/vendor-pages),
never the network. Volatile fields (timestamps) are normalized before the
byte-for-byte comparison; prices, ceiling, retail formula and source hashes
must match exactly.
"""
import hashlib, json, re, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests' / 'fixtures' / 'vendor-pages'
PRICING = 'https://api-docs.deepseek.com/quick_start/pricing/'
API = 'https://api-docs.deepseek.com/api/create-chat-completion/'

RUNNER = r'''
import runpy, sys
from delivery_api import vendor_sources as vs
import pathlib, hashlib
fx = pathlib.Path(sys.argv[1])
bodies = {}
for u in (sys.argv[2], sys.argv[3]):
    name = 'page-%s.html' % hashlib.sha256(u.encode()).hexdigest()[:8]
    bodies[u] = (fx / name).read_text()
vs.fetch_verified_source = lambda u: bodies[u]
out_dir, pin_path = sys.argv[4], sys.argv[5]
sys.argv = ['pin.py', out_dir]
runpy.run_path(pin_path, run_name='__main__')
'''

def _original_embedded_pin():
    # Frozen copy of the heredoc embedded in scripts/ops/go-live.sh before the
    # extraction (source: commit 5169b9c, scripts/ops/go-live.sh). Frozen as a
    # fixture because HEAD no longer embeds it; extraction must stay byte-identical.
    return (FIXTURES.parent / 'pin-embedded-original.py').read_text()

def _run_pin(pin_source, out_dir):
    pin_path = out_dir / 'pin-under-test.py'
    pin_path.write_text(pin_source)
    proc = subprocess.run(
        [str(REPO / '.venv/bin/python'), '-c', RUNNER,
         str(FIXTURES), PRICING, API, str(out_dir), str(pin_path)],
        capture_output=True, text=True, cwd=REPO,
        env={'PYTHONPATH': str(REPO / 'src'), 'PATH': '/usr/bin:/bin'})
    assert proc.returncode == 0, proc.stderr[-500:] + proc.stdout[-500:]
    return (out_dir / 'cloud-models.env').read_text(), proc.stdout

def _normalize(text):
    # fetched_at / READ AT timestamps and the date-stamped price version are volatile;
    # everything else - prices, ceiling, retail, source urls and hashes - is compared exactly.
    text = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+00:00', '<TS>', text)
    text = re.sub(r'deepseek-flash-\d{8}', 'deepseek-flash-<DATE>', text)
    return text

def test_extracted_pin_matches_the_embedded_original(tmp_path):
    original = _original_embedded_pin()
    shared = (REPO / 'scripts' / 'ops' / 'pin.py').read_text()
    out_a, out_b = tmp_path / 'a', tmp_path / 'b'
    out_a.mkdir(); out_b.mkdir()
    env_a, print_a = _run_pin(original, out_a)
    env_b, print_b = _run_pin(shared, out_b)
    assert _normalize(env_a) == _normalize(env_b)
    assert _normalize(print_a) == _normalize(print_b)

def test_pin_output_values_grounded(tmp_path):
    """The pinned settings carry the reviewed values: historical 128000/16384
    safety ceiling (never the vendor maximum), retail from the code defaults,
    DeepSeek source URLs and sha256 evidence."""
    env_text, printed = _run_pin((REPO / 'scripts' / 'ops' / 'pin.py').read_text(), tmp_path)
    rows = json.loads(re.search(r'^VMODEL_REVIEWED_MODEL_PRICES=(.*)$', env_text, re.M).group(1))
    ceiling = json.loads(re.search(r'^VMODEL_REVIEWED_CONTEXT_CEILING=(.*)$', env_text, re.M).group(1))
    (row,) = rows
    assert row['provider'] == 'deepseek' and row['model'] == 'deepseek-flash'
    assert ceiling['context_tokens'] == 128000 and ceiling['maximum_output_tokens'] == 16384
    assert ceiling['protective_run_ceiling'] is True
    scale = 10 ** 9
    assert row['retail_units_per_credit'] == scale
    assert row['retail_formula']['price_per_token'] == '0.0000125'
    assert row['retail_formula']['price_per_10ms'] == '0.0000005'
    assert row['retail_formula']['usd_per_credit'] == '1.00'
    # vendor facts from the captured pages: peak $0.30 miss / $0.006 hit / $1.20 out per 1M
    rates = row['provider_snapshot']['published_usd_per_million']
    assert rates['peak'] == {'cache_hit': '0.006', 'cache_miss': '0.3', 'output': '1.2'}
    assert rates['off_peak'] == {'cache_hit': '0.003', 'cache_miss': '0.15', 'output': '0.6'}
    sources = {s['url'] for s in row['provider_snapshot']['sources']}
    assert sources == {PRICING, API}
    assert all(len(s['sha256']) == 64 for s in row['provider_snapshot']['sources'])

def test_pin_fails_closed_on_changed_vendor_page(tmp_path):
    """A changed pricing page must stop the pin (SourceChanged), never produce settings."""
    import shutil
    broken = tmp_path / 'fx'
    shutil.copytree(FIXTURES, broken)
    name = 'page-%s.html' % hashlib.sha256(PRICING.encode()).hexdigest()[:8]
    page = (broken / name).read_text().replace('$0.3', '$9.99', 1)
    assert page != (broken / name).read_text() or '$0.3' not in page
    (broken / name).write_text(page)
    pin_path = tmp_path / 'pin.py'
    pin_path.write_text((REPO / 'scripts' / 'ops' / 'pin.py').read_text())
    proc = subprocess.run(
        [str(REPO / '.venv/bin/python'), '-c', RUNNER,
         str(broken), PRICING, API, str(tmp_path), str(pin_path)],
        capture_output=True, text=True, cwd=REPO,
        env={'PYTHONPATH': str(REPO / 'src'), 'PATH': '/usr/bin:/bin'})
    assert proc.returncode == 2
    assert 'PIN-FAIL' in proc.stdout
    assert not (tmp_path / 'cloud-models.env').exists()
