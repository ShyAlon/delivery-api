"""Unmounted payment router factory; disabled by default, even with injections.

Mount/enable only under separately reviewed rollout. Owner resolver must validate
current auth session and provisioned account scale from trusted server sources.
No default repository, processor credentials or request-selected owner/price.
"""
import secrets
from fastapi import APIRouter,Request,HTTPException
from .payment_errors import PaymentRejected,PaymentUncertain
from .payment_offers import purchase_intent


def create_router(*,service=None,owner_resolver=None,mutation_guard=None,enabled=False):
    router=APIRouter()
    def require_enabled():
        if enabled is not True or service is None or owner_resolver is None:raise HTTPException(404,'Not found')
    def owner(request):
        require_enabled()
        # Resolver's result is not any request-supplied identity/scale.
        if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'Cross-site payment request denied')
        if request.method not in ('GET','HEAD'):
            if not callable(mutation_guard):raise HTTPException(403,'Payment mutation guard required')
            mutation_guard(request)
        result=owner_resolver(request)
        if not isinstance(result,dict) or set(result)!={'owner_id','units_per_credit'}:
            raise HTTPException(401,'Active provisioned account required')
        return result
    def owned_purchase(request,purchase_id):
        identity=owner(request)
        try:row=service.repo.get_purchase(purchase_id)
        except PaymentRejected:raise HTTPException(404,'Not found')
        if row['intent'].owner_id!=identity['owner_id']:raise HTTPException(404,'Not found')
        return row
    async def body(request):
        value=bytearray()
        async for chunk in request.stream():
            value.extend(chunk)
            if len(value)>262144:raise HTTPException(413,'Request too large')
        return bytes(value)
    def safe(action):
        try:return action()
        except PaymentUncertain:raise HTTPException(409,'Payment awaiting reconciliation; do not create another purchase')
        except (PaymentRejected,ValueError):raise HTTPException(409,'Payment verification or review required')

    @router.post('/api/payments/purchases')
    async def create(request:Request):
        identity=owner(request)
        from .payment_json import strict_json as _json
        try:payload=_json(await body(request))
        except PaymentRejected:raise HTTPException(422,'Offer ID required')
        if set(payload)!={'offer_id'}:raise HTTPException(422,'Only a server offer ID may be selected')
        try:intent=purchase_intent(purchase_id=secrets.token_hex(16),offer_id=payload['offer_id'],**identity)
        except (ValueError,TypeError):raise HTTPException(422,'Reviewed offer and provisioned account required')
        service.repo.create_purchase(intent)
        # Return durable local ID before starting any remote purchase. A lost
        # response here is safe because it has not charged/created a remote order.
        return {'purchase_id':intent.purchase_id,'currency':intent.currency,'amount_minor':intent.amount_minor}

    @router.post('/api/payments/purchases/{purchase_id}/checkout')
    def checkout(purchase_id:str,request:Request):
        owned_purchase(request,purchase_id)
        return safe(lambda:service.create(purchase_id))

    @router.post('/api/payments/purchases/{purchase_id}/capture')
    def capture(purchase_id:str,request:Request):
        owned_purchase(request,purchase_id)
        return safe(lambda:service.capture(purchase_id))

    @router.get('/api/payments/purchases/{purchase_id}')
    def status(purchase_id:str,request:Request):
        row=owned_purchase(request,purchase_id)
        return {'purchase_id':purchase_id,'state':row['state'],'credit_posted':row['credit_posted']}

    @router.post('/api/payments/webhook')
    async def webhook(request:Request):
        require_enabled()
        raw=await body(request)
        return safe(lambda:service.webhook(raw,dict(request.headers)))
    return router
