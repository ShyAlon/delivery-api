"""PayPal HTTP boundary with explicitly injected client and token supplier.

No default HTTP client, environment secrets, OAuth login or application wiring.
Unit tests use httpx.MockTransport exclusively. Token supplier and merchant/app
binding require separately verified provisioning before activation.
"""
import re
from .paypal_adapter import PayPalBinding,PaymentRejected,PaymentUncertain


class PayPalHTTPTransport:
    def __init__(self,*,binding,client,token_supplier):
        if not isinstance(binding,PayPalBinding) or not callable(token_supplier):
            raise PaymentRejected('explicit provisioned binding and token supplier required')
        self.binding=binding;self.client=client;self.token_supplier=token_supplier
        self.base='https://api-m.paypal.com' if binding.live_mode else 'https://api-m.sandbox.paypal.com'

    def request(self,method,path,payload=None,headers=None):
        allowed=(method=='POST' and path in ('/v2/checkout/orders','/v1/notifications/verify-webhook-signature')) or bool(
            re.fullmatch(r'/v2/checkout/orders/[A-Za-z0-9_-]{1,127}'+('/capture' if method=='POST' else ''),path) and method in ('GET','POST')) or bool(
            method=='GET' and re.fullmatch(r'/v2/payments/captures/[A-Za-z0-9_-]{1,127}',path))
        if not allowed:raise PaymentRejected('fixed PayPal API path required')
        token=self.token_supplier()
        if not isinstance(token,str) or not token or len(token)>8192 or any(ord(c)<33 for c in token):
            raise PaymentRejected('valid transient server OAuth token required')
        headers=dict(headers or {})
        if any(k.lower() in ('authorization','host') for k in headers):raise PaymentRejected('caller cannot replace account authority')
        headers.update(Authorization='Bearer '+token,Accept='application/json',**{'Content-Type':'application/json'})
        import httpx
        try:
            response=self.client.request(method,self.base+path,json=payload if method=='POST' else None,
                headers=headers,timeout=20,follow_redirects=False)
        except httpx.TransportError as exc:
            # Do not include request bodies, tokens or remote error detail.
            raise PaymentUncertain('PayPal response unavailable; reconcile existing request') from exc
        if response.status_code>=500 or response.status_code in (408,429):
            raise PaymentUncertain('PayPal operation uncertain; reconcile before retry')
        if not 200<=response.status_code<300:raise PaymentRejected('PayPal request rejected')
        if len(response.content)>1048576:raise PaymentUncertain('PayPal response exceeds safe bound')
        try:result=response.json()
        except ValueError as exc:raise PaymentUncertain('PayPal response malformed; reconcile') from exc
        if not isinstance(result,dict):raise PaymentUncertain('PayPal response object required')
        return result
