"""Private PostgreSQL payment ingestion, separate from tenant-serving identity.

Not configured/mounted by the application. Requires a dedicated restricted login
that can assume only the payment role. No source evidence can choose this DSN.
"""
import time
from contextlib import contextmanager
from .payment_contract import PurchaseIntent,ProcessorBinding,prepare_fulfillment
from .payment_errors import PaymentRejected,PaymentUncertain


class PaymentRepository:
    def __init__(self,dsn,binding):
        import psycopg
        if not isinstance(binding,ProcessorBinding):raise PaymentRejected('explicit payment merchant binding required')
        self.dsn=dsn;self.binding=binding
        with psycopg.connect(dsn) as c:
            unsafe=c.execute("SELECT 1 FROM pg_roles WHERE (rolsuper OR rolbypassrls) AND pg_has_role(session_user,oid,'MEMBER') LIMIT 1").fetchone()
            if unsafe:raise PaymentRejected('payment login may not assume RLS bypass')
            if c.execute("SELECT pg_has_role(session_user,'requi_backend','MEMBER')").fetchone()[0]:
                raise PaymentRejected('payment login must be separate from tenant-serving role')
            c.execute('SET LOCAL ROLE requi_payment')
            owned=c.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR pg_has_role(session_user,c.relowner,'MEMBER')) LIMIT 1").fetchone()
            if owned:raise PaymentRejected('payment login cannot own/unprotect application tables')

    @contextmanager
    def transaction(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.dsn,row_factory=dict_row) as c:
            with c.transaction():
                c.execute('SET LOCAL ROLE requi_payment')
                yield c

    @property
    def namespace(self):return (self.binding.processor,self.binding.merchant_id,self.binding.live_mode)

    def _purchase(self,row):
        if not row:raise PaymentRejected('private purchase not found')
        row=dict(row)
        row['owner_id']=str(row['owner_id'])
        intent=PurchaseIntent(**{k:row[k] for k in PurchaseIntent.__dataclass_fields__})
        if intent.digest!=row['intent_digest']:raise PaymentRejected('persisted intent digest mismatch')
        row['intent']=intent
        return row

    def _select(self,c,purchase_id,*,lock=False):
        return self._purchase(c.execute('SELECT * FROM payment_purchases WHERE purchase_id=%s AND (processor,merchant_account,live_mode)=(%s,%s,%s)'+(' FOR UPDATE' if lock else ''),(purchase_id,*self.namespace)).fetchone())

    def create_purchase(self,intent):
        if not isinstance(intent,PurchaseIntent) or intent.currency!='USD':raise PaymentRejected('reviewed immutable USD offer required')
        fields=list(PurchaseIntent.__dataclass_fields__)
        now=time.time()
        with self.transaction() as c:
            c.execute('INSERT INTO payment_purchases ('+','.join(fields)+',processor,merchant_account,live_mode,intent_digest,created_at,updated_at) VALUES ('+','.join(['%s']*14)+') ON CONFLICT(purchase_id) DO NOTHING',tuple(getattr(intent,f) for f in fields)+self.namespace+(intent.digest,now,now))
            row=self._select(c,intent.purchase_id)
            if row['intent']!=intent:raise PaymentRejected('conflicting purchase replay')
        return row

    def get_purchase(self,purchase_id):
        with self.transaction() as c:return self._select(c,purchase_id)

    def bind_request_ids(self,purchase_id,create_key,capture_key):
        from uuid import UUID
        if str(UUID(create_key))!=create_key or str(UUID(capture_key))!=capture_key or create_key==capture_key:raise PaymentRejected('distinct canonical request IDs required')
        with self.transaction() as c:
            row=self._select(c,purchase_id,lock=True)
            if row['create_request_id'] and (str(row['create_request_id'])!=create_key or str(row['capture_request_id'])!=capture_key):raise PaymentRejected('request-key replay conflict')
            c.execute('UPDATE payment_purchases SET create_request_id=%s,capture_request_id=%s WHERE purchase_id=%s',(create_key,capture_key,purchase_id))

    def begin_remote_operation(self,purchase_id,operation,*,replay_window_seconds):
        if operation not in ('create','capture'):raise PaymentRejected('known remote operation required')
        if type(replay_window_seconds) is not int or not 0<replay_window_seconds<=86400:raise PaymentRejected('explicit processor replay window required')
        field=operation+'_started_at'
        with self.transaction() as c:
            row=self._select(c,purchase_id,lock=True)
            if row['state']=='operator_review':raise PaymentRejected('purchase requires review')
            if not row['create_request_id']:raise PaymentRejected('persisted remote request keys required')
            now=time.time()
            if row[field] is None:
                c.execute('UPDATE payment_purchases SET '+field+'=%s WHERE purchase_id=%s',(now,purchase_id))
            elif now-row[field]>=replay_window_seconds or now<row[field]:
                raise PaymentUncertain('remote request-key window uncertain; reconcile with operator, never generate a new charge key')

    def _order_lock(self,c,order_id):
        if not order_id:return
        import hashlib
        key='payment-order:'+'|'.join(map(str,self.namespace))+':'+order_id
        lock=int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big',signed=True)
        c.execute('SELECT pg_advisory_xact_lock(%s)',(lock,))

    def bind_order(self,purchase_id,order_id):
        if not isinstance(order_id,str) or not 1<=len(order_id)<=127:raise PaymentRejected('bounded processor order ID required')
        with self.transaction() as c:
            self._order_lock(c,order_id)
            row=self._select(c,purchase_id,lock=True)
            if not row['create_request_id']:raise PaymentRejected('request IDs must be persisted before remote create')
            if row['order_id'] and row['order_id']!=order_id:raise PaymentRejected('different order cannot replace immutable binding')
            held=c.execute("SELECT 1 FROM payment_events WHERE (processor,merchant_account,live_mode)=(%s,%s,%s) AND order_id=%s AND action='review' LIMIT 1",(*self.namespace,order_id)).fetchone()
            state='operator_review' if held or row['state']=='operator_review' else row['state']
            c.execute('UPDATE payment_purchases SET order_id=%s,state=%s,updated_at=%s WHERE purchase_id=%s',(order_id,state,time.time(),purchase_id))

    def find_purchase(self,*,order_id=None,capture_id=None):
        if not order_id and not capture_id:return None
        with self.transaction() as c:
            rows=c.execute('SELECT * FROM payment_purchases WHERE (processor,merchant_account,live_mode)=(%s,%s,%s) AND (order_id=%s OR capture_id=%s)',(*self.namespace,order_id,capture_id)).fetchall()
            if len(rows)>1:raise PaymentRejected('conflicting event purchase binding')
            if not rows:return None
            row=self._purchase(rows[0])
            if order_id and row['order_id']!=order_id:raise PaymentRejected('event order differs from persisted order')
            if capture_id and row['capture_id'] and row['capture_id']!=capture_id:raise PaymentRejected('event capture differs from persisted capture')
            return row

    def _event(self,c,receipt):
        if receipt is None:return None
        if not isinstance(receipt['event_id'],str) or not 1<=len(receipt['event_id'])<=127:raise PaymentRejected('bounded processor event ID required')
        # Serialize each event to make receipt + effect one atomic result, even
        # when simultaneous workers resolve it to different purchases.
        import hashlib
        key='|'.join(map(str,self.namespace))+':'+receipt['event_id']
        lock=int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big',signed=True)
        c.execute('SELECT pg_advisory_xact_lock(%s)',(lock,))
        row=c.execute('SELECT * FROM payment_events WHERE (processor,merchant_account,live_mode,event_id)=(%s,%s,%s,%s)',(*self.namespace,receipt['event_id'])).fetchone()
        if row:
            if row['raw_digest']!=receipt['digest'] or row['kind']!=receipt['kind']:raise PaymentRejected('conflicting event replay')
            return row['result']
        return None

    def event_result(self,receipt):
        with self.transaction() as c:return self._event(c,receipt)

    def _receipt(self,c,receipt,result,purchase_id=None):
        if receipt is None:return
        from psycopg.types.json import Jsonb
        c.execute('INSERT INTO payment_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(*self.namespace,receipt['event_id'],receipt['digest'],receipt['kind'],purchase_id,Jsonb(result),time.time(),receipt.get('order_id'),receipt.get('payment_id'),receipt.get('action')))

    def fulfill(self,intent,evidence,plan,*,receipt=None):
        expected=prepare_fulfillment(intent,evidence,expected_processor=self.binding.processor,expected_merchant=self.binding.merchant_id,expected_live_mode=self.binding.live_mode)
        if plan!=expected:raise PaymentRejected('credit posting plan mismatch')
        with self.transaction() as c:
            prior=self._event(c,receipt)
            if prior is not None:return prior
            self._order_lock(c,self._select(c,intent.purchase_id)['order_id'])
            row=self._select(c,intent.purchase_id,lock=True)
            held=c.execute("SELECT 1 FROM payment_events WHERE (processor,merchant_account,live_mode)=(%s,%s,%s) AND order_id=%s AND action='review' LIMIT 1",(*self.namespace,row['order_id'])).fetchone()
            if held:
                c.execute("UPDATE payment_purchases SET state='operator_review' WHERE purchase_id=%s",(intent.purchase_id,))
                row['state']='operator_review'
            if row['intent']!=intent or not row['order_id']:raise PaymentRejected('current immutable order binding required')
            posted=c.execute('SELECT public.requi_post_payment_credit(%s,%s,%s,%s,%s,%s,%s) AS posted',(intent.purchase_id,intent.digest,plan['payment_key'],evidence.payment_id,time.time(),evidence.processor_fee_minor,evidence.net_received_minor)).fetchone()['posted']
            result={'state':'operator_review' if row['state']=='operator_review' else 'fulfilled','credit_posted':posted}
            self._receipt(c,receipt,result,intent.purchase_id)
            return result

    def _record(self,purchase,receipt,state):
        with self.transaction() as c:
            prior=self._event(c,receipt)
            if prior is not None:return prior
            self._order_lock(c,receipt.get('order_id'))
            purchase_id=purchase['intent'].purchase_id if purchase else None
            if purchase is None and receipt.get('action')=='review' and receipt.get('order_id'):
                found=c.execute('SELECT purchase_id FROM payment_purchases WHERE (processor,merchant_account,live_mode)=(%s,%s,%s) AND order_id=%s',(*self.namespace,receipt['order_id'])).fetchone()
                if found:purchase_id=found['purchase_id'];purchase=self._select(c,purchase_id);state='operator_review'
            if purchase:
                row=self._select(c,purchase_id,lock=True)
                if state=='operator_review':
                    c.execute("UPDATE payment_purchases SET state='operator_review',updated_at=%s WHERE purchase_id=%s",(time.time(),purchase_id))
                elif row['state']=='operator_review':state='operator_review'
                elif row['credit_posted']:state='fulfilled'
            result={'state':state,'credit_posted':False}
            self._receipt(c,receipt,result,purchase_id)
            return result

    def record_waiting(self,purchase,receipt):return self._record(purchase,receipt,'awaiting_payment')
    def record_review(self,purchase,receipt):return self._record(purchase,receipt,'operator_review')
    def record_unmatched(self,receipt):return self._record(None,receipt,'unmatched_operator_review')
    def record_ignored(self,receipt):return self._record(None,receipt,'ignored')

    def receiving_cost_report(self,*,start_at,end_at,owner_id=None):
        import math
        from .payment_reporting import receiving_report
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (start_at,end_at)) or not start_at<end_at or end_at-start_at>366*86400:
            raise PaymentRejected('explicit bounded report period required')
        owner_filter=' AND c.owner_id=%s' if owner_id else ''
        params=(*self.namespace,start_at,end_at)+((owner_id,) if owner_id else ())
        with self.transaction() as c:
            rows=c.execute('SELECT c.processor,c.merchant_account,c.live_mode,c.owner_id,p.offer_version,p.offer_id,c.currency,c.amount_minor,c.processor_fee_minor,c.net_received_minor,p.state FROM payment_credits c JOIN payment_purchases p USING(purchase_id) WHERE (c.processor,c.merchant_account,c.live_mode)=(%s,%s,%s) AND c.posted_at>=%s AND c.posted_at<%s'+owner_filter,params).fetchall()
        for row in rows:row['owner_id']=str(row['owner_id'])
        result=receiving_report(rows)
        result.update(start_at=start_at,end_at=end_at)
        return result

    def reconciliation_candidates(self,*,limit=100):
        """Bounded private queue; does not imply a scheduled/live read is enabled."""
        if type(limit) is not int or not 1<=limit<=100:raise PaymentRejected('bounded reconciliation page required')
        with self.transaction() as c:
            rows=c.execute("SELECT purchase_id,order_id,create_request_id,capture_request_id,create_started_at,capture_started_at FROM payment_purchases WHERE (processor,merchant_account,live_mode)=(%s,%s,%s) AND state='awaiting_payment' AND create_started_at IS NOT NULL ORDER BY created_at,purchase_id LIMIT %s",(*self.namespace,limit+1)).fetchall()
        return {'purchases':rows[:limit],'truncated':len(rows)>limit,'automatic_retry':False}
