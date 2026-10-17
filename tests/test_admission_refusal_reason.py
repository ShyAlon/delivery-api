"""The worker is told why an admission was refused; the reply never carries raw database or I/O text."""
import json

from delivery_api.admission_transport import worker_admission_channel
from delivery_api.attempt_ledger import AdmissionDenied


class Gateway:
    def __init__(self, exc):
        self.exc = exc

    def reserve_serialized(self, **kw):
        raise self.exc


def _ask(exc, payload=None):
    payload = payload if payload is not None else {'attempt_id': 'a', 'stage': 's', 'provider': 'p', 'model': 'm',
                                                   'serialized_request': 'x', 'max_output_tokens': 1}
    with worker_admission_channel(Gateway(exc)) as child:
        f = child.makefile('rwb')
        f.write(json.dumps({'action': 'reserve_serialized', 'payload': payload}).encode() + b'\n'); f.flush()
        reply = json.loads(f.readline())
        f.close()
    return reply


def test_refusal_text_is_returned_for_our_own_denials():
    reply = _ask(AdmissionDenied('explicit reviewed estimate policy required'))
    assert reply == {'ok': False, 'error': 'worker attempt admission denied',
                     'reason': 'AdmissionDenied: explicit reviewed estimate policy required'}


def test_other_exceptions_are_named_by_class_only():
    reply = _ask(RuntimeError('connection to postgres://user:secret@db/x failed'))
    assert reply['ok'] is False and reply['reason'] == 'RuntimeError'
    assert 'secret' not in json.dumps(reply)


def test_malformed_request_is_a_plain_refusal_with_its_class():
    reply = _ask(AdmissionDenied('unused'), payload={'wrong': 1})
    assert reply['ok'] is False and reply['reason'] == 'ValueError'


def test_success_reply_is_unchanged():
    class Ok:
        def reserve_serialized(self, **kw):
            return 'attempt-1'
    with worker_admission_channel(Ok()) as child:
        f = child.makefile('rwb')
        f.write(json.dumps({'action': 'reserve_serialized', 'payload': {'attempt_id': 'a', 'stage': 's', 'provider': 'p', 'model': 'm', 'serialized_request': 'x', 'max_output_tokens': 1}}).encode() + b'\n'); f.flush()
        assert json.loads(f.readline()) == {'ok': True, 'result': 'attempt-1'}
        f.close()
