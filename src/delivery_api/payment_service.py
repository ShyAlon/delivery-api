"""Processor orchestration against a durable repository, no mounted endpoints.

Repository owns transactional binding, event receipts, unique payment credit
posting, owner balance update and review races. Verified payloads are data.
"""
from .payment_errors import PaymentRejected
from .payment_contract import prepare_fulfillment


class PaymentService:
    def __init__(self,adapter,repository):
        # Prevent accidentally combining a sandbox adapter with another ledger
        # account or live merchant namespace.
        ledger=getattr(repository,'binding',None)
        binding=adapter.binding
        if ledger is not None and (ledger.processor,ledger.merchant_id,ledger.live_mode)!=(binding.processor,binding.merchant_id,binding.live_mode):
            raise PaymentRejected('processor/ledger namespace mismatch')
        self.adapter=adapter;self.repo=repository

    def create(self,purchase_id):
        purchase=self.repo.get_purchase(purchase_id)
        if purchase['order_id']:return self.adapter.approval(purchase['intent'],purchase['order_id'])
        intent=purchase['intent']
        self.repo.bind_request_ids(purchase_id,self.adapter.request_id(intent,'create'),self.adapter.request_id(intent,'capture'))
        self.repo.begin_remote_operation(purchase_id,'create',replay_window_seconds=self.adapter.replay_window_seconds)
        result=self.adapter.create(intent)
        self.repo.bind_order(purchase_id,result['order_id'])
        return self.adapter.approval(intent,result['order_id'])

    def capture(self,purchase_id):
        purchase=self.repo.get_purchase(purchase_id)
        if not purchase['order_id']:raise PaymentRejected('persisted order binding required')
        # A completed order can be reconciled without another charge attempt.
        evidence=self.adapter.completed_evidence(purchase['intent'],purchase['order_id'],event_id='capture-reconcile-'+purchase['order_id'])
        if evidence is None:
            self.repo.begin_remote_operation(purchase_id,'capture',replay_window_seconds=self.adapter.replay_window_seconds)
            evidence=self.adapter.capture(purchase['intent'],purchase['order_id'])
        if evidence is None:return {'state':'awaiting_payment','credit_posted':False}
        return self._fulfill(purchase,evidence)

    def _fulfill(self,purchase,evidence,receipt=None):
        plan=prepare_fulfillment(purchase['intent'],evidence,expected_processor=self.adapter.binding.processor,
            expected_merchant=self.adapter.binding.merchant_id,expected_live_mode=self.adapter.binding.live_mode)
        return self.repo.fulfill(purchase['intent'],evidence,plan,receipt=receipt)

    def webhook(self,raw,headers):
        event,digest=self.adapter.verify_event(raw,headers)
        notice=self.adapter.payment_notice(event)
        receipt={'event_id':event['id'],'digest':digest,'kind':notice['kind'],
            'order_id':notice['order_id'],'payment_id':notice['payment_id'],'action':notice['action']}
        previous=self.repo.event_result(receipt)
        if previous is not None:return previous
        if notice['action']=='ignored':return self.repo.record_ignored(receipt)
        purchase=self.repo.find_purchase(order_id=notice['order_id'],capture_id=notice['payment_id'])
        if purchase is None:return self.repo.record_unmatched(receipt)
        if notice['action']=='paid':
            evidence=self.adapter.completed_evidence(purchase['intent'],purchase['order_id'],event_id=event['id'],capture_id=notice['payment_id'])
            if evidence is None:return self.repo.record_waiting(purchase,receipt)
            return self._fulfill(purchase,evidence,receipt=receipt)
        if notice['action']=='pending':return self.repo.record_waiting(purchase,receipt)
        if notice['action']=='review':return self.repo.record_review(purchase,receipt)
        raise PaymentRejected('unsupported processor lifecycle outcome')
