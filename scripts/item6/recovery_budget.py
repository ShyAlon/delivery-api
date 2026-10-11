"""Run-local maximum provider expense admission; accepted attempts still settle."""
from decimal import Decimal
from delivery_api.attempt_ledger import AdmissionDenied

class RecoveryBudgetGateway:
    def __init__(self,inner,*,original_job_ids,maximum_expense_units):
        if type(maximum_expense_units) is not int or maximum_expense_units<=0:raise ValueError('approved positive expense cap required')
        self.inner=inner;self.original=set(original_job_ids);self.limit=maximum_expense_units
    def __getattr__(self,key):return getattr(self.inner,key)
    def __setattr__(self,key,value):
        if key=='compute_meter' and 'inner' in self.__dict__:setattr(self.inner,key,value)
        else:object.__setattr__(self,key,value)
    def reserve_serialized(self,**request):
        # Reserve and cap check share same scoped transaction+owner lock.
        from delivery_api.postgres_store import _transactional
        from delivery_api.prepaid import owner_lock
        @_transactional
        def bounded(store):
            owner_lock(store)
            attempt=self.inner.reserve_serialized(**request)
            rows=store._db.execute('SELECT * FROM provider_attempts').fetchall()
            cost=sum(r['expense_actual'] if r['state']=='settled' else r['expense_reserved']
              for r in rows if r['job_id'] not in self.original and r['state']!='cancelled')
            row=store.provider_attempt(attempt)
            if not any(r['attempt_id']==attempt for r in rows):cost+=row['expense_reserved']
            if cost>self.limit:raise AdmissionDenied('reviewed continuation provider expense cap exhausted')
            return attempt
        return bounded(self.inner.store)
