"""Pre-call positive balance, post-call exact debit. No retail reservation.

Callers use this within the same transaction as provider start/settlement.
Unknown outcomes keep pending charge evidence, never imply a zero debit.
"""
import hashlib,time
from .tenancy import current_scope
from .attempt_ledger import AdmissionDenied,_integer
from . import credit_audit


def owner_lock(store):
    key=int.from_bytes(hashlib.sha256(current_scope().owner_id.encode()).digest()[:8],'big',signed=True)
    store._execute_native('SELECT pg_advisory_xact_lock(%s)',(key,)).fetchone()


def authorize(store,attempt):
    owner_lock(store)
    account=store._execute_native('SELECT * FROM credit_accounts WHERE owner_id=%s FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account or account['balance_units']<=0:raise AdmissionDenied('positive prepaid balance required')
    # Operator-reviewed quote rates must use the same credit-unit scale.
    if attempt['quote'].get('retail_units_per_credit') != account['units_per_credit']:
        raise AdmissionDenied('reviewed retail quote scale differs from prepaid account')
    store._execute_native("INSERT INTO credit_charges(attempt_id,owner_id,state,created_at,units_per_credit) VALUES (%s,%s,'pending',%s,%s)",(attempt['attempt_id'],current_scope().owner_id,time.time(),account['units_per_credit']), write=True)


def debit(store,attempt,amount,*,already_posted=False):
    owner_lock(store)
    charge=store._execute_native('SELECT * FROM credit_charges WHERE attempt_id=%s FOR UPDATE',(attempt['attempt_id'],)).fetchone()
    if not charge:return  # Explicit legacy fake-only path, never a prepaid call.
    if not _integer(amount):raise AdmissionDenied('exact settled retail debit required')
    if charge['state']=='settled':
        if charge['charged_units']!=amount:raise AdmissionDenied('conflicting prepaid charge replay')
        return
    account=store._execute_native('SELECT * FROM credit_accounts WHERE owner_id=%s FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account:
        from .observability import event as activity
        activity("credits.settlement_held","A prepaid settlement is held: the account disappeared",attempt_id=str(attempt['attempt_id']),reason="account_missing")
        raise AdmissionDenied('prepaid account disappeared; settlement held')
    if charge['units_per_credit'] is None or charge['units_per_credit']!=account['units_per_credit']:
        from .observability import event as activity
        activity("credits.settlement_held","A prepaid settlement is held: the credit scale changed or is unknown",attempt_id=str(attempt['attempt_id']),reason="scale_changed")
        raise AdmissionDenied('prepaid charge scale changed or unknown; settlement held')
    now=time.time()
    if not already_posted:
        credit_audit.attribute(store._execute_native,'debit','system:prepaid',None,str(attempt['attempt_id']),marker='%s')
        store._execute_native('UPDATE credit_accounts SET balance_units=balance_units-%s,updated_at=%s WHERE owner_id=%s',(amount,now,current_scope().owner_id), write=True)
    store._execute_native("UPDATE credit_charges SET state='settled',charged_units=%s,settled_at=%s WHERE attempt_id=%s",(amount,now,attempt['attempt_id']), write=True)
