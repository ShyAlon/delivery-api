"""The private workspace's reviewed authority inventory, not an auto-grant."""
PURPOSES={
 'analytics.read':'Account credit balances, settled/held provider expense and vendor status',
 'sessions.read':'Cross-owner session metadata',
 'debug.read':'Current-owner Worker Jobs, Stop/hide and server debug evidence',
 'logs.read':'Curated audit/release evidence and eligible current-owner proof-checked resolution',
 'usage.read':'Recorded tokens and customer credit usage',
 'payments.read':'Payment receipts, fees and net totals (read only)',
 'services.operate':'Review/approve model defaults and edit the operator expense ceiling; no paid-run activation',
}
DEFAULT_CAPABILITIES=frozenset(PURPOSES)
EXCLUDED={'credits.allocate':'Separate confirmed credit allocation; not offered by this workspace',
 'users.suspend':'Not offered by this workspace','messages.prepare':'Not offered by this workspace',
 'sessions.operate':'No cross-owner session mutation offered by this workspace'}


def effective(reader,store):
    result={}
    for cap in PURPOSES:
        try:
            authority=reader.principal.authorize(store,cap)
            with reader.db.transaction() as conn:
                conn.execute('SET LOCAL ROLE requi_operator_read')
                allowed=conn.execute('SELECT public.requi_operator_read_allowed(%s,%s) AS allowed',
                    (cap,authority['registry_version'])).fetchone()
                result[cap]=bool(allowed and allowed['allowed'])
        except PermissionError:result[cap]=False
    return {'capabilities':result,'purposes':PURPOSES,'paid_activation':False,'write_off':False}
