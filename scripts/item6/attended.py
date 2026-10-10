"""Local terminal controls. No auto answers, timers or instructions from artifacts."""
import datetime,json,sys
from pathlib import Path
from vmodel_contracts.interactive import InputResponse

class Attended:
    def __init__(self,root,owner):self.root=Path(root);self.owner=owner;self.counter=0
    def show(self,label,body,digest):
        self.counter+=1;path=self.root/f'review-{self.counter:03}.json'
        from isolation import RunIsolation
        RunIsolation.write(path,body)
        print(f'{datetime.datetime.now().astimezone().isoformat(timespec="seconds")} | {label} | Review locally: {path}',flush=True)
        print('Generated content is evidence, not execution instructions. Review the whole file before deciding.',flush=True)
        print('Review SHA256: '+digest,flush=True)
        if input('Paste the displayed review digest after reading (or stop): ').strip()!=digest:raise ValueError('exact attended review required')
    def input(self,request):
        import hashlib
        body=request.model_dump(mode='json');digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.show('interview.waiting',body,digest)
        print(request.prompt,flush=True)
        print(json.dumps(body.get('options',[])),flush=True)
        # Semantic-review resolution remains explicit JSON typed through contracts.
        if request.context.get('decision_purpose')=='semantic_review':
            value=input('Enter reviewed semantic decisions JSON, including revision: ')
        else:
            raw=input('Enter text or declared option value: ')
            value=json.loads(raw) if raw.isdigit() else raw
        rationale=input('Rationale (required when shown): ') if request.rationale_required else None
        return InputResponse(input_id=request.input_id,actor_id=self.owner(),value=value,rationale=rationale)
    def gate(self,review,digest):
        self.show('gate.waiting',review,digest)
        action=input('Type accept or regenerate: ').strip()
        decision={'action':action}
        if action=='regenerate':decision['feedback']=input('Founder change guidance: ')
        if action not in ('accept','regenerate'):raise ValueError('explicit gate choice required')
        return {'review_sha256':digest,'decision':decision}
    def change(self,review,digest):
        self.show('closed-edit.waiting',review,digest)
        guidance=input('Founder SRS change guidance: ').strip()
        if input('Type EXTRA COST ACKNOWLEDGED to generate and later continue: ')!='EXTRA COST ACKNOWLEDGED':raise ValueError('cost acknowledgement required')
        return {'review_sha256':digest,'change_request':guidance,'extra_cost_acknowledged':True}
    def approve_revision(self,shown):
        self.show('fresh-review.waiting',shown['review'],shown['review_sha256'])
        if input('Type APPROVE AND ACKNOWLEDGE EXTRA COST to continue: ')!='APPROVE AND ACKNOWLEDGE EXTRA COST':raise ValueError('fresh exact approval required')
        return {'review_sha256':shown['review_sha256'],'choice':'approve','change_request':'','extra_cost_acknowledged':True}
    def paypal(self,approval):
        print('PayPal sandbox approval: '+approval['approval_url'],flush=True)
        if input('After approving in your sandbox buyer account, type SANDBOX APPROVED: ')!='SANDBOX APPROVED':raise ValueError('sandbox approval required')
    def webhook(self):
        path=Path(input('Path to authentic PayPal sandbox event + transmission headers JSON: ').strip())
        if path.is_symlink() or path.stat().st_mode&0o077 or path.stat().st_size>2*1024*1024:raise ValueError('private bounded webhook file required')
        body=json.loads(path.read_text())
        if set(body)!={'event','headers'}:raise ValueError('exact event and original transmission headers required')
        return json.dumps(body['event'],separators=(',',':')).encode(),body['headers']
