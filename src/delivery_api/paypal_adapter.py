"""PayPal Orders v2 preparation with an injected authenticated transport.

No network implementation, credentials, account discovery or route activation.
Transport must bind a single sandbox/live app+merchant and never follow body URLs.
"""
import hashlib,re
from dataclasses import dataclass
from uuid import uuid5,NAMESPACE_URL
from .payment_contract import PurchaseIntent,ProcessorEvidence,ProcessorBinding


from .payment_errors import PaymentUncertain,PaymentRejected
from .payment_json import strict_json as _json


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,127}',value):
        raise PaymentRejected('bounded PayPal resource ID required')
    return value


def amount_text(intent):
    if intent.currency!='USD':raise PaymentRejected('only exact USD offers supported')
    return f'{intent.amount_minor//100}.{intent.amount_minor%100:02d}'


def _amount(value,intent):
    if not isinstance(value,dict) or value.get('currency_code')!=intent.currency or value.get('value')!=amount_text(intent):
        raise PaymentRejected('amount or currency mismatch')


@dataclass(frozen=True)
class PayPalBinding:
    merchant_id:str
    webhook_id:str
    live_mode:bool=False

    @property
    def processor(self):return 'paypal'

    @property
    def ledger_binding(self):return ProcessorBinding('paypal',self.merchant_id,self.live_mode)

    def __post_init__(self):
        _id(self.merchant_id);_id(self.webhook_id)
        if type(self.live_mode) is not bool:raise PaymentRejected('explicit sandbox/live binding required')


class PayPalAdapter:
    replay_window_seconds=5*3600  # Conservative margin within documented 6h retention.
    def __init__(self,transport,binding):
        if not isinstance(binding,PayPalBinding):raise PaymentRejected('explicit merchant/app binding required')
        # Transport factory must verify OAuth app/account binding before use.
        if getattr(transport,'binding',None)!=binding:raise PaymentRejected('transport merchant/app/mode mismatch')
        self.transport=transport;self.binding=binding

    def request_id(self,intent,operation):
        if operation not in ('create','capture'):raise PaymentRejected('unknown payment operation')
        return str(uuid5(NAMESPACE_URL,'requi:paypal:'+str(self.binding.live_mode)+':'+self.binding.merchant_id+':'+intent.digest+':'+operation))

    def create(self,intent):
        payload={'intent':'CAPTURE','purchase_units':[{'reference_id':intent.purchase_id,
            'custom_id':intent.digest,'invoice_id':intent.purchase_id,'payee':{'merchant_id':self.binding.merchant_id},
            'amount':{'currency_code':intent.currency,'value':amount_text(intent)}}]}
        # Caller must durably save request ID and frozen intent BEFORE this call.
        response=self.transport.request('POST','/v2/checkout/orders',payload,
            {'PayPal-Request-Id':self.request_id(intent,'create'),'Prefer':'return=representation'})
        self.validate_order(response,intent)
        return {'order_id':_id(response.get('id')),'status':response.get('status')}

    def approval(self,intent,order_id):
        order_id=_id(order_id)
        order=self.transport.request('GET','/v2/checkout/orders/'+order_id)
        self.validate_order(order,intent,order_id=order_id)
        if order.get('status')=='COMPLETED':return {'order_id':order_id,'approval_url':None}
        from urllib.parse import urlsplit,parse_qs
        urls=[]
        host='www.paypal.com' if self.binding.live_mode else 'www.sandbox.paypal.com'
        for link in order.get('links',[]):
            if not isinstance(link,dict) or link.get('rel') not in ('approve','payer-action'):continue
            value=link.get('href');url=urlsplit(value if isinstance(value,str) else '')
            if url.scheme!='https' or url.netloc!=host or url.fragment or url.path!='/checkoutnow' or parse_qs(url.query).get('token')!=[order_id]:
                raise PaymentRejected('unexpected PayPal approval link')
            urls.append(value)
        if len(urls)!=1:raise PaymentRejected('one authoritative PayPal approval link required')
        return {'order_id':order_id,'approval_url':urls[0]}

    def validate_order(self,order,intent,*,order_id=None):
        if not isinstance(order,dict) or (order_id is not None and order.get('id')!=order_id) or order.get('intent')!='CAPTURE':
            raise PaymentRejected('order identity or intent mismatch')
        _id(order.get('id'))
        units=order.get('purchase_units')
        if not isinstance(units,list) or len(units)!=1 or not isinstance(units[0],dict):
            raise PaymentRejected('exactly one immutable purchase unit required')
        unit=units[0]
        if unit.get('reference_id')!=intent.purchase_id or unit.get('custom_id')!=intent.digest or unit.get('invoice_id')!=intent.purchase_id:
            raise PaymentRejected('immutable purchase binding mismatch')
        if unit.get('payee',{}).get('merchant_id')!=self.binding.merchant_id:
            raise PaymentRejected('merchant mismatch')
        _amount(unit.get('amount'),intent)
        return unit

    def capture(self,intent,order_id):
        order_id=_id(order_id)
        before=self.transport.request('GET','/v2/checkout/orders/'+order_id)
        self.validate_order(before,intent,order_id=order_id)
        if before.get('status')=='APPROVED':
            # Never use a newly generated key after an ambiguous timeout.
            self.transport.request('POST','/v2/checkout/orders/'+order_id+'/capture',{},
                {'PayPal-Request-Id':self.request_id(intent,'capture'),'Prefer':'return=representation'})
        elif before.get('status') not in ('COMPLETED',):
            return None
        return self.completed_evidence(intent,order_id,event_id='capture-reconcile-'+order_id)

    def completed_evidence(self,intent,order_id,*,event_id,capture_id=None):
        order_id=_id(order_id)
        order=self.transport.request('GET','/v2/checkout/orders/'+order_id)
        unit=self.validate_order(order,intent,order_id=order_id)
        if order.get('status')!='COMPLETED':return None
        captures=unit.get('payments',{}).get('captures',[])
        if not isinstance(captures,list) or len(captures)!=1:
            raise PaymentRejected('one final capture required')
        found=captures[0];cid=_id(found.get('id'))
        if capture_id is not None and cid!=capture_id:raise PaymentRejected('capture/order mismatch')
        if found.get('status')!='COMPLETED':return None
        if found.get('final_capture') is not True:raise PaymentRejected('final full capture required')
        _amount(found.get('amount'),intent)
        current=self.transport.request('GET','/v2/payments/captures/'+cid)
        if not isinstance(current,dict) or current.get('id')!=cid:raise PaymentRejected('capture lookup mismatch')
        if current.get('status')!='COMPLETED':return None
        related=current.get('supplementary_data',{}).get('related_ids',{})
        if related.get('order_id')!=order_id:raise PaymentRejected('authoritative capture/order binding required')
        if current.get('final_capture') is not True:raise PaymentRejected('final capture required')
        _amount(current.get('amount'),intent)
        payee=current.get('payee')
        if payee is not None and payee.get('merchant_id')!=self.binding.merchant_id:
            raise PaymentRejected('capture merchant mismatch')
        fee=None;net=None
        breakdown=current.get('seller_receivable_breakdown')
        if breakdown is not None:
            if not isinstance(breakdown,dict):raise PaymentRejected('invalid gross/fee/net breakdown')
            _amount(breakdown.get('gross_amount'),intent)
            def minor(value):
                if not isinstance(value,dict) or value.get('currency_code')!=intent.currency:
                    raise PaymentRejected('fee/net currency mismatch')
                text=value.get('value')
                if not isinstance(text,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,16})\.[0-9]{2}',text):
                    raise PaymentRejected('exact fee/net decimal required')
                return int(text.replace('.',''))
            fee=minor(breakdown.get('paypal_fee'));net=minor(breakdown.get('net_amount'))
            if fee+net!=intent.amount_minor:raise PaymentRejected('gross/fee/net mismatch')
        return ProcessorEvidence('paypal',self.binding.merchant_id,_id(event_id),cid,
            intent.purchase_id,intent.digest,intent.currency,intent.amount_minor,'paid',self.binding.live_mode,fee,net)

    def capture_order_id(self,capture_id):
        capture_id=_id(capture_id)
        capture=self.transport.request('GET','/v2/payments/captures/'+capture_id)
        if not isinstance(capture,dict) or capture.get('id')!=capture_id:
            raise PaymentRejected('adverse capture lookup mismatch')
        return _id(capture.get('supplementary_data',{}).get('related_ids',{}).get('order_id'))

    def payment_notice(self,event):
        """Translate PayPal notification into a neutral lifecycle notice."""
        kind=event.get('event_type');resource=event['resource']
        if not isinstance(kind,str) or not kind or len(kind)>200:
            raise PaymentRejected('bounded event type required')
        action={'PAYMENT.CAPTURE.COMPLETED':'paid','PAYMENT.CAPTURE.PENDING':'pending',
            'PAYMENT.CAPTURE.REFUNDED':'review','PAYMENT.CAPTURE.REVERSED':'review',
            'CUSTOMER.DISPUTE.CREATED':'review','CUSTOMER.DISPUTE.UPDATED':'review',
            'CUSTOMER.DISPUTE.RESOLVED':'review'}.get(kind,'ignored')
        order_id=resource.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
        capture_id=None
        if kind in ('PAYMENT.CAPTURE.COMPLETED','PAYMENT.CAPTURE.PENDING','PAYMENT.CAPTURE.REVERSED'):
            capture_id=_id(resource.get('id'))
        elif kind=='PAYMENT.CAPTURE.REFUNDED':
            capture_id=resource.get('supplementary_data',{}).get('related_ids',{}).get('capture_id')
            if not capture_id:
                # Official refund resource identifies parent capture with an up
                # HATEOAS link. Extract only a strictly validated ID, never
                # navigate to an arbitrary event-supplied URL.
                candidates=[]
                allowed=('api-m.paypal.com','api.paypal.com') if self.binding.live_mode else ('api-m.sandbox.paypal.com','api.sandbox.paypal.com')
                for link in resource.get('links',[]):
                    if not isinstance(link,dict) or link.get('rel')!='up' or link.get('method')!='GET':continue
                    from urllib.parse import urlsplit
                    url=urlsplit(link.get('href',''))
                    match=re.fullmatch(r'/v2/payments/captures/([A-Za-z0-9_-]{1,127})',url.path)
                    if url.scheme!='https' or url.netloc not in allowed or url.query or url.fragment or not match:
                        raise PaymentRejected('invalid parent capture link')
                    candidates.append(match.group(1))
                if len(candidates)==1:capture_id=candidates[0]
                elif len(candidates)>1:raise PaymentRejected('ambiguous refund capture link')
        elif kind.startswith('CUSTOMER.DISPUTE.'):
            transactions=resource.get('disputed_transactions',[])
            if isinstance(transactions,list) and len(transactions)==1:
                capture_id=transactions[0].get('seller_transaction_id')
        if capture_id:
            authoritative_order=self.capture_order_id(capture_id)
            if order_id is not None and authoritative_order!=order_id:
                raise PaymentRejected('event capture/order lookup conflict')
            order_id=authoritative_order
        return {'action':action,'order_id':order_id,'payment_id':capture_id,'kind':kind}

    def verify_event(self,raw,headers):
        event=_json(raw)
        lower={}
        for key,value in headers.items():
            key=str(key).lower()
            if key in lower:raise PaymentRejected('ambiguous duplicated webhook header')
            lower[key]=value
        names={'auth_algo':'paypal-auth-algo','cert_url':'paypal-cert-url',
            'transmission_id':'paypal-transmission-id','transmission_sig':'paypal-transmission-sig',
            'transmission_time':'paypal-transmission-time'}
        payload={}
        for key,header in names.items():
            value=lower.get(header)
            if not isinstance(value,str) or not value or len(value)>1000 or any(ord(c)<32 for c in value):
                raise PaymentRejected('required signed webhook headers missing/invalid')
            payload[key]=value
        # Remote verify endpoint consumes the cert URL as data. We do not fetch
        # it or any resource/href URL supplied by a notification.
        payload.update(webhook_id=self.binding.webhook_id,webhook_event=event)
        result=self.transport.request('POST','/v1/notifications/verify-webhook-signature',payload)
        if not isinstance(result,dict) or result.get('verification_status')!='SUCCESS':
            raise PaymentRejected('webhook verification failed')
        _id(event.get('id'))
        if not isinstance(event.get('resource'),dict):raise PaymentRejected('event resource required')
        return event,hashlib.sha256(raw).hexdigest()
