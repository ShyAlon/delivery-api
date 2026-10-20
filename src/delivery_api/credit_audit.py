"""Say who changed a credit balance and why, for the database's credit ledger.

The credit_accounts trigger records every balance change itself. It reads four transaction-local settings to
name the kind, actor, reason and reference. Call attribute() in the same transaction, before the UPDATE.
A change made without it is still recorded, as 'unattributed'."""

KINDS=('topup','grant','debit','adjustment','rescale')


def attribute(execute,kind,actor,reason=None,reference=None,marker='?'):
    if kind not in KINDS:raise ValueError('unknown credit ledger kind')
    if not actor:raise ValueError('a credit change needs an actor')
    m=marker
    cur=execute("SELECT set_config('requi.ledger_kind',%s,true),set_config('requi.ledger_actor',%s,true),set_config('requi.ledger_reason',%s,true),set_config('requi.ledger_reference',%s,true)"%(m,m,m,m),
        (kind,str(actor)[:200],(reason or '')[:500],(reference or '')[:240]))
    fetch=getattr(cur,'fetchone',None)
    if fetch:fetch()
