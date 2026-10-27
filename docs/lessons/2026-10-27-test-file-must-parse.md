# A test file that cannot be parsed is a suite that silently never runs

2026-10-27: tests/test_progress_seven_phase_parity.py (from 291e158) used a
PEP 701 f-string - nested same-type quotes inside an f-string expression,
legal only on Python 3.12+. This repo's venv is 3.10, so pytest reported one
collection error and the file's parity coverage sat dead while the suite
looked green. Fix: hoist nested-quote payloads out of f-strings. Guard:
tests/test_tests_parse.py ast-parses every test file under the running
interpreter, so the next py3.12-ism fails loudly instead of skipping a file.
