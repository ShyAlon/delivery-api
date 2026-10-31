"""Bounded operator signal, never provider/user content or a delivery receipt."""
import json,logging


def emit(event,reason):
    allowed={
        'tripwire_breach':{'session_expense_limit','platform_rolling_24h_expense_limit','own_test_rolling_24h_expense_limit'},
        'provider_failure':{'provider_outcome_incomplete'},
        'unresolved_accounting':{'provider_usage','compute_usage','unknown_provider_outcome','own_test_unknown_provider_outcome','own_test_expense_history_unverifiable'},
    }
    if reason not in allowed.get(event,set()):return
    try:
        logging.getLogger('requi.alert').warning('REQUI_ALERT '+json.dumps(
            {'version':1,'event':event,'reason':reason,'action':'operator_review'},sort_keys=True,separators=(',',':')))
    except Exception:pass  # Log delivery cannot change accepted accounting or reopen admission.
