"""Strict supported OpenAI-compatible provider HTTP boundary for real journeys."""
import json


def read_request(handler):
    assert handler.path=='/v1/chat/completions'
    assert handler.headers.get('Content-Type')=='application/json'
    raw=handler.rfile.read(int(handler.headers['content-length']))
    payload=json.loads(raw)
    assert handler.headers.get('Accept')==('text/event-stream' if payload.get('stream') else 'application/json')
    assert isinstance(payload.get('model'),str) and payload['model']
    assert isinstance(payload.get('messages'),list)
    assert not handler.headers.get('Transfer-Encoding')
    return payload
