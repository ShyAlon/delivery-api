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
    store._db.execute('SELECT pg_advisory_xact_lock(?)',(key,)).fetchone()


def authorize(store,attempt):
    owner_lock(store)
    account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account or account['balance_units']<=0:raise AdmissionDenied('positive prepaid balance required')
    # Operator-reviewed quote rates must use the same credit-unit scale.
    if attempt['quote'].get('retail_units_per_credit') != account['units_per_credit']:
        raise AdmissionDenied('reviewed retail quote scale differs from prepaid account')
    store._db.execute("INSERT INTO credit_charges(attempt_id,owner_id,state,created_at,units_per_credit) VALUES (?,?,'pending',?,?)",(attempt['attempt_id'],current_scope().owner_id,time.time(),account['units_per_credit']))


def debit(store,attempt,amount,*,already_posted=False):
    owner_lock(store)
    charge=store._db.execute('SELECT * FROM credit_charges WHERE attempt_id=? FOR UPDATE',(attempt['attempt_id'],)).fetchone()
    if not charge:return  # Explicit legacy fake-only path, never a prepaid call.
    if not _integer(amount):raise AdmissionDenied('exact settled retail debit required')
    if charge['state']=='settled':
        if charge['charged_units']!=amount:raise AdmissionDenied('conflicting prepaid charge replay')
        return
    account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account:raise AdmissionDenied('prepaid account disappeared; settlement held')
    if charge['units_per_credit'] is None or charge['units_per_credit']!=account['units_per_credit']:
        raise AdmissionDenied('prepaid charge scale changed or unknown; settlement held')
    now=time.time()
    if not already_posted:
        credit_audit.attribute(store._db.execute,'debit','system:prepaid',None,str(attempt['attempt_id']))
        store._db.execute('UPDATE credit_accounts SET balance_units=balance_units-?,updated_at=? WHERE owner_id=?',(amount,now,current_scope().owner_id))
    store._db.execute("UPDATE credit_charges SET state='settled',charged_units=?,settled_at=? WHERE attempt_id=?",(amount,now,attempt['attempt_id']))
