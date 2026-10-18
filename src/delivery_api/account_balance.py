"""A signed-in customer's own prepaid credit balance, read-only. It never reads another owner's row."""
from decimal import Decimal


def read(db,owner_id,usd_per_credit):
    """Credits and USD for this owner. An owner with no credit account has zero credits."""
    row=db.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=?',(owner_id,)).fetchone()
    if row is None:return {'credits':0.0,'usd':0.0}
    per=int(row['units_per_credit'])
    if per<=0:raise ValueError('credit account has an invalid unit scale')
    credits=Decimal(int(row['balance_units']))/Decimal(per)
    return {'credits':float(round(credits,6)),'usd':float(round(credits*Decimal(str(usd_per_credit)),6))}
