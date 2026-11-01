"""Mandatory scenario: missing prerequisites, no tests, skips and failures are red."""
import pathlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

root=pathlib.Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as temp:
    report=pathlib.Path(temp)/'results.xml'
    result=subprocess.run([sys.executable,'-m','pytest','-q',
        'tests/test_fresh_start_acceptance.py','--junitxml='+str(report)],cwd=root)
    if result.returncode:raise SystemExit(result.returncode)
    if not report.exists():raise SystemExit('Acceptance produced no result')
    cases=ET.parse(report).findall('.//testcase')
    if len(cases)!=1 or any(c.find(tag) is not None for c in cases for tag in ('skipped','failure','error')):
        raise SystemExit('Acceptance requires exactly one passing scenario, zero skips')
