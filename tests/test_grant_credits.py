"""ops grant-credits: free credits and an admission row for named users, written only after one typed yes."""
import ast
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
    assert "read -rs CONN" in SCRIPT and '"$1"' not in SCRIPT and "$@" not in SCRIPT


def test_it_warns_loudly_when_the_operator_ends_with_no_credits():
    assert "WARNING: your own account" in PYTHON and "has 0 credits" in PYTHON
