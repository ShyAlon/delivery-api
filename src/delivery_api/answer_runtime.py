"""Inactive parent-only answer adapter. No route/factory selects this path.

Start and finish are one claimed pipeline batch each. Provider calls and compute
stay in memory. No journal, dispatch marker, monetary cap or per-call DB access.
The host still owns job/input publication, shutdown and accounting-mode cutover.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64,hashlib,json,threading,time
from decimal import Decimal
from uuid import uuid4
from .answer_pricing import AnswerPricing
from .retail_pricing import decimal_value
from .tenancy import current_scope


class AnswerReceiptStore:
    def __init__(self,postgres):self.postgres=postgres

    def _call(self,sql,params):
        # No automatic retry: COMMIT may have succeeded even if the ack was lost.
        # Caller replays the receipt key explicitly, never the provider request.
        from .postgres_scope import _SETUP,note_statement,request_stats
        scope=current_scope();connection,reused=self.postgres._checkout();started=time.monotonic()
        stats=request_stats.get()
        try:
            if connection.info.transaction_status.name!='IDLE':raise RuntimeError('answer batch requires an idle connection')
            previous_autocommit=connection.autocommit
            connection.autocommit=True  # Prevent psycopg's implicit BEGIN from adding its own Sync.
            with connection.pipeline():
                connection.execute('BEGIN')
                connection.execute(_SETUP,(scope.owner_id,scope.session_id or ''))
                result=connection.execute(sql,params)
                connection.execute('COMMIT')
            row=result.fetchone()
            if row is None:raise RuntimeError('answer receipt missing')
            receipt=row['receipt']
            note_statement('BEGIN + answer identity [batched]',0,0)
            note_statement('COMMIT',0,0)
            connection.autocommit=previous_autocommit
        except BaseException:
            self.postgres._drop(connection)
            raise
        else:self.postgres._release(connection)
        finally:
            elapsed=time.monotonic()-started
            # One synchronization wall time, not four invented statement trips.
            note_statement('answer receipt [BEGIN + identity + function + COMMIT pipeline]',elapsed)
            if stats is not None:
                stats.transactions+=1;stats.queries+=1;stats.db_seconds+=elapsed
                if reused:stats.reused+=1
                else:stats.opened+=1
        return receipt

    def start(self,*,answer_id,job_id,input_id,input_digest,pricing):
        return self._call('SELECT public.requi_answer_start(%s,%s,%s,%s,%s::jsonb) AS receipt',
            (answer_id,job_id,input_id,input_digest,json.dumps(pricing.snapshot())))

    def finish(self,answer_id,manifest):
        return self._call('SELECT public.requi_answer_finish(%s,%s::jsonb) AS receipt',(answer_id,json.dumps(manifest)))


class AnswerRun:
    @classmethod
    def begin(cls,store,*,answer_id,job_id,input_id,input_digest,pricing):
        if not isinstance(pricing,AnswerPricing):raise TypeError('frozen answer pricing required')
        receipt=store.start(answer_id=answer_id,job_id=job_id,input_id=input_id,input_digest=input_digest,pricing=pricing)
        run=cls(store,receipt,execute_allowed=receipt['state']=='pending' and receipt['job_id']==job_id)
        run._log('start')
        return run

    def __init__(self,store,receipt,*,execute_allowed):
        self.store=store;self._receipt=deepcopy(receipt);self.execute_allowed=execute_allowed
        self._attempts={};self._unknown=0;self._compute=None;self._active=0
        self._lock=threading.RLock();self._manifest=None;self._closed=receipt['state']=='settled'

    @property
    def receipt(self):return deepcopy(self._receipt)
    @property
    def unknown_calls(self):return self._unknown

    @contextmanager
    def provider_call(self):
        with self._lock:
            if not self.execute_allowed or self._closed or self._manifest is not None:
                raise RuntimeError('original answer job is not accepting provider work')
            self._active+=1
        try:yield
        finally:
            with self._lock:self._active-=1

    def record_usage(self,attempt_id,phase,raw):
        from .deepseek_usage import normalize
        try:usage=normalize(raw)
        except (ValueError,RuntimeError) as error:
            # AdmissionDenied derives from RuntimeError. Unknown is not zero;
            # it does not block delivering the provider's successful response.
            with self._lock:self._unknown+=1
            return False
        evidence={'attempt_id':attempt_id,'phase':phase,'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens']}
        with self._lock:
            if self._closed or self._manifest is not None:raise RuntimeError('answer usage already frozen')
            old=self._attempts.get(attempt_id)
            if old is not None and old!=evidence:raise ValueError('conflicting in-memory answer usage')
            self._attempts[attempt_id]=evidence
        return True

    def unknown(self):
        with self._lock:self._unknown+=1

    def record_compute(self,value):
        duration=decimal_value(value,'deterministic_ms')
        with self._lock:
            if self._compute is not None and self._compute!=duration:raise ValueError('conflicting answer compute')
            if self._closed or self._manifest is not None:raise RuntimeError('answer usage already frozen')
            self._compute=duration

    def finish(self):
        with self._lock:
            if self._closed:return self.receipt
            if self._active:raise RuntimeError('provider calls must be quiescent before correction')
            if self._unknown or self._compute is None:
                # Retain the upfront debit. No fabricated usage or automatic refund.
                self._closed=True;self._log('finish');return self.receipt
            if self._manifest is None:
                self._manifest={'attempts':sorted(deepcopy(list(self._attempts.values())),key=lambda x:x['attempt_id']),
                                'deterministic_ms':str(self._compute)}
            self._receipt=self.store.finish(self._receipt['answer_id'],self._manifest)
            self._closed=True
            self._log('finish')
            return self.receipt

    def _log(self,event):
        # Statistics for the whole cohort, never raw input, request bytes or provider responses.
        from .diagnostic_evidence import record
        record('answer.accounting.'+event,**self.summary())

    def summary(self):
        r=self.receipt
        return {'answer_id':r['answer_id'],'state':r['state'],'estimate_units':r['estimate_units'],
                'actual_units':r.get('actual_units'),'correction_units':r.get('correction_units'),
                'overrun':r.get('overrun'),'completed_calls':len(self._attempts),'unknown_calls':self._unknown,
                'deterministic_ms':str(self._compute) if self._compute is not None else None,
                'started_at':r['started_at'],'settled_at':r.get('settled_at')}


class AnswerProviderBroker:
    def __init__(self,run,*,provider,model,client,endpoint,headers,stop=None):
        if provider!='deepseek':raise ValueError('answer paid adapter requires a reviewed DeepSeek route')
        from .provider_http import ParentProviderHTTP
        self.run=run;self.provider=provider;self.model=model;self.http=ParentProviderHTTP(client,endpoint,headers)
        self.stop=stop or threading.Event();self.lock=threading.Lock();self._replies={};self.timings=[]

    def dispatch(self,value):
        from .child_provider_broker import ProviderCall,ChildProviderBroker,MAX_REQUEST,ProviderRefused
        from .provider_request import request_contract
        from .provider_http import ProviderHTTPError
        call=ProviderCall.model_validate(value)
        if (call.provider,call.model)!=(self.provider,self.model):raise ValueError('provider/model differs from answer snapshot')
        raw=base64.b64decode(call.serialized_request,validate=True)
        if not 0<len(raw)<=MAX_REQUEST:raise ValueError('supported serialized request required')
        request_contract(raw,call.model)
        digest=hashlib.sha256(raw).hexdigest();key=(call.stage,call.call_id)
        with self.lock:
            if self.stop.is_set():raise InterruptedError('answer stopped')
            prior=self._replies.get(key)
            if prior is not None:
                if prior[0]!=digest:raise ValueError('logical answer call changed')
                return deepcopy(prior[1])
            with self.run.provider_call():
                attempt=uuid4().hex;started=time.monotonic()
                try:body,content_type=self.http.send(raw)
                except BaseException as error:
                    self.run.unknown()
                    if type(error) is ProviderHTTPError:raise ProviderRefused(error) from error
                    raise
                try:usage=ChildProviderBroker._usage(body,content_type)
                except Exception:
                    self.run.unknown()
                else:self.run.record_usage(attempt,call.stage,usage)
                reply={'status':200,'content_type':content_type,'body':base64.b64encode(body).decode(),'sha256':hashlib.sha256(body).hexdigest()}
                self._replies[key]=(digest,deepcopy(reply))
                self.timings.append({'stage':call.stage,'transport_ms':int((time.monotonic()-started)*1000)})
                return reply


class AnswerComputeAccounting:
    """Existing sequenced child clock semantics, in memory only. No transcript IO."""
    def __init__(self,run):
        from .child_compute_events import ChildComputeEvents
        self.run=run;self.events=ChildComputeEvents();self.started=False;self.settled=False;self.value=None
    def begin(self):
        if self.started:raise RuntimeError('compute already started')
        self.started=True
    def accept(self,event):
        if not self.started or self.settled:raise RuntimeError('compute not open')
        self.events.accept(event)
    def finish(self):
        if self.settled:return self.value
        self.value=self.events.milliseconds();self.run.record_compute(self.value);self.settled=True
        return self.value
    def abort(self):pass  # Missing complete clock leaves E retained, never guessed.


class AnswerAccountingMode:
    """Explicit internal injection only; no production constructor selects it.

    The existing reviewed task gateway supplies its frozen retail rates, not the
    child or current process defaults. Transport still belongs to PaidChildRuntime.
    """
    def __init__(self,*,start_credits='0.1'):
        from .retail_pricing import RetailRates
        # Validate E without taking a model/rate snapshot prematurely.
        self.start_credits=AnswerPricing(start_credits,RetailRates.from_environment({})).start_credits

    def begin(self,store,operation,input_payload,gateway):
        if operation.operation.kind!='interview.answer' or operation.binding.provider!='deepseek':
            raise ValueError('two-touch mode currently supports paid interview answers only')
        if getattr(gateway,'retail_rates',None) is None:
            raise ValueError('reviewed task retail rates required')
        payload=json.dumps(input_payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        identity=[str(operation.binding.owner_id),operation.binding.session_id,
                  operation.binding.generation,operation.operation.wait_id]
        answer_id=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
        run=AnswerRun.begin(AnswerReceiptStore(store._postgres),answer_id=answer_id,
            job_id=operation.binding.job_id,input_id=operation.operation.wait_id,
            input_digest=hashlib.sha256(payload).hexdigest(),
            pricing=AnswerPricing(self.start_credits,gateway.retail_rates))
        if not run.execute_allowed:
            raise RuntimeError('answer already belongs to original job '+run.receipt['job_id']+'; recover without replacement dispatch')
        return run
