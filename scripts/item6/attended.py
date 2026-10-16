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
        # The decision that follows is bound to this digest: gates return it and the pipeline rejects a mismatch;
        # the digest and review file are in the console log and evidence. No pasted digest.
    def input(self,request):
        import hashlib
        body=request.model_dump(mode='json');digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.show('interview.waiting',body,digest)
        print(request.prompt,flush=True)
        print(json.dumps(body.get('options',[])),flush=True)
        # Semantic-review resolution remains explicit JSON typed through contracts.
        if request.context.get('decision_purpose')=='semantic_review':
            review=(request.context.get('evidence') or {}).get('semantic_review')
            if isinstance(review,dict) and review.get('findings') and review.get('revision'):value=self.semantic_value(review)
            else:value=input('Enter reviewed semantic decisions JSON, including revision: ')
        else:
            raw=input('Enter text or declared option value: ')
            value=json.loads(raw) if raw.isdigit() else raw
        rationale=input('Rationale (required when shown): ') if request.rationale_required else None
        return InputResponse(input_id=request.input_id,actor_id=self.owner(),value=value,rationale=rationale)
    def semantic_value(self,review,ask=None):
        """One choice per reviewed item; the harness assembles the exact JSON (revision-bound, actor-bound) the engine verifies."""
        ask=ask or input;items={}
        for f in review['findings']:items.setdefault(f['item_id'],[]).append(f)
        decisions=[]
        for n,(item_id,findings) in enumerate(items.items(),1):
            print(f'--- item {n} of {len(items)}: {item_id}',flush=True)
            for f in findings:
                print(f"  [{f.get('kind','semantic')}] rule {f['rule']} at {'/'.join(str(x) for x in f['path'])}: {f['reason']}",flush=True)
                print('  original: '+json.dumps(f['original'],ensure_ascii=False)[:1500],flush=True)
            choice=ask('Type accept, remove or replace: ').strip()
            if choice not in ('accept','remove','replace'):raise ValueError('explicit item choice required')
            decision={'item_id':item_id,'action':choice,'actor':self.owner(),'rationale':ask('Rationale (Enter to skip): ').strip()}
            if choice=='replace':decision['replacement']=json.loads(ask('Replacement value as JSON: '))
            decisions.append(decision)
        return json.dumps({'revision':review['revision'],'decisions':decisions},sort_keys=True)
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
        # The host-side watcher exports the capture for THIS run (invoice id) into the private mount; wait for it.
        # Falls back to the typed path after the wait, so a missing watcher cannot strand the run.
        import os,time
        folder=Path(os.environ.get('ITEM6_WEBHOOK_DIR','/private'));deadline=time.monotonic()+float(os.environ.get('ITEM6_WEBHOOK_WAIT_SECONDS','1800'))
        found=[]
        print('Waiting for the PayPal sandbox event file to be exported by the host watcher (no input needed)...',flush=True)
        while time.monotonic()<deadline:
            found=sorted(folder.glob('webhook-*.json'))
            if found:break
            time.sleep(5)
        if len(found)>1:raise ValueError('exactly one exported webhook file required')
        path=found[0] if found else Path(input('Path to authentic PayPal sandbox event + transmission headers JSON: ').strip())
        if path.is_symlink() or path.stat().st_mode&0o077 or path.stat().st_size>2*1024*1024:raise ValueError('private bounded webhook file required')
        body=json.loads(path.read_text())
        if body.get('format')=='item6-paypal-raw-v1':
            from receiver.receiver import decode_envelope
            return decode_envelope(body)
        if set(body)!={'event','headers'}:raise ValueError('exact event and original transmission headers required')
        return json.dumps(body['event'],separators=(',',':')).encode(),body['headers']
