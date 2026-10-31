"""ops grant-credits: free credits and an admission row for named users, written only after one typed yes."""
import ast
import io
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "ops" / "grant-credits.sh").read_text()
PYTHON = SCRIPT.split("<<'PYE'\n")[1].split("\nPYE")[0]


def test_the_embedded_python_parses():
    ast.parse(PYTHON)


def test_nothing_is_written_before_the_typed_yes():
    yes = PYTHON.index("!='yes'")
    for write in ("INSERT INTO credit_adjustments", "INSERT INTO admission_limits", "UPDATE credit_accounts", "INSERT INTO credit_accounts"):
        assert PYTHON.index(write) > yes, write
    assert "conn.commit()" in PYTHON and PYTHON.index("conn.commit()") > yes


def test_the_default_grant_is_50_and_the_typo_guards_stay():
    assert "[50]: " in SCRIPT and "DEF=${DEF:-50}" in SCRIPT
    assert "MAX_PER_PERSON=D(100)" in PYTHON and "MAX_TOTAL=D(500)" in PYTHON


def test_other_users_get_credits_as_their_only_ceiling_and_only_the_operator_has_an_expense_cap():
    other = [line for line in PYTHON.splitlines() if "INSERT INTO admission_limits" in line and "DO NOTHING" in line]
    assert len(other) == 1 and "expense_limit" not in other[0]
    own = [line for line in PYTHON.splitlines() if "INSERT INTO admission_limits" in line and "DO UPDATE" in line]
    assert len(own) == 1 and "OWN_LIMIT_USD_UNITS" in own[0]
    assert "OWN_LIMIT_USD_UNITS=2*10**12" in PYTHON


def test_the_connection_string_is_typed_hidden_and_never_an_argument():
    assert 'ops_settings REQUI_ADMIN_DATABASE_URL' in SCRIPT
    assert 'CONN=$REQUI_ADMIN_DATABASE_URL' in SCRIPT
    assert '\"$1\"' not in SCRIPT and '$@' not in SCRIPT
    # Loader is covered by real PTY no-echo tests, not a retired shell spelling.
    loader=(Path(__file__).resolve().parents[1]/'scripts/ops/settings.py').read_text()
    assert 'termios.ECHO' in loader and 'hidden=True' in loader


def test_it_warns_loudly_when_the_operator_ends_with_no_credits():
    assert "WARNING: your own account" in PYTHON and "has 0 credits" in PYTHON


def _helpers(**names):
    start = PYTHON.index("def fmt(d):")
    end = PYTHON.index("lines=[l.rstrip")
    scope = {"sys": sys, **names}
    exec(PYTHON[start:end], scope)
    return scope


def test_credit_amounts_print_as_plain_numbers_not_decimal_exponents():
    from decimal import Decimal as D
    fmt = _helpers(die=None)["fmt"]
    assert fmt(D(50)) == "50" and fmt(D("5E+1")) == "50" and fmt(D(0)) == "0" and fmt(D("0.5")) == "0.5"
    assert fmt(D("3026580000") / 10**9) == "3.02658" and fmt(D("100")) == "100" and fmt(D("-2.50")) == "-2.5"


def test_no_credit_amount_is_printed_with_normalize():
    assert ".normalize()" not in PYTHON.replace("credits.normalize(),today", "")
    assert "credits.normalize(),today" in PYTHON   # the idempotence key keeps its format so a repeat grant is still recognised


def test_the_typed_yes_is_read_from_the_terminal_because_stdin_holds_the_script():
    assert "input(" not in PYTHON and "ask('\\nType yes" in PYTHON


def test_a_missing_terminal_ends_with_a_clear_abort_and_never_a_traceback(monkeypatch):
    aborts = []
    def die(code, msg, fix):
        aborts.append((code, msg, fix)); raise SystemExit(1)
    scope = _helpers(die=die)
    def no_tty(*a, **k):
        raise OSError("no tty")
    monkeypatch.setattr("builtins.open", no_tty)
    try:
        scope["ask"]("Type yes: ")
    except SystemExit:
        pass
    assert aborts and aborts[0][0] == "NO-TERMINAL" and "normal terminal window" in aborts[0][2]


def test_the_typed_answer_is_read_from_the_terminal_and_trimmed(monkeypatch, capsys):
    real_open = open
    def fake(path, *a, **k):
        return io.StringIO("yes \n") if path == "/dev/tty" else real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", fake)
    scope = _helpers(die=lambda *a: (_ for _ in ()).throw(AssertionError("aborted")))
    assert scope["ask"]("Type yes: ") == "yes"
    assert "Type yes: " in capsys.readouterr().out
