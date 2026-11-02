"""HTTP-envelope contract at the exact-byte parent provider boundary."""
import json
import httpx
import pytest
from delivery_api.provider_http import ParentProviderHTTP,ProviderHTTPError


@pytest.mark.parametrize('provider',['deepseek','ollama'])
@pytest.mark.parametrize('stream',[False,True])
def test_exact_body_and_parent_headers_without_redirects(provider,stream):
    raw=json.dumps({'model':'fixture','messages':[],'stream':stream},indent=2).encode();seen=[]
    def send(request):
        seen.append(request)
        assert request.content==raw
        assert request.headers['content-type']=='application/json'
        assert request.headers['accept']==('text/event-stream' if stream else 'application/json')
        assert request.headers.get('authorization')==('Bearer fixture' if provider=='deepseek' else None)
        return httpx.Response(200,headers={'content-type':request.headers['accept']+'; charset=utf-8'},content=b'result')
    with httpx.Client(transport=httpx.MockTransport(send),trust_env=False,follow_redirects=False) as client:
        http=ParentProviderHTTP(client,'https://fixture.invalid/v1/chat/completions',{'Authorization':'Bearer fixture'} if provider=='deepseek' else {})
        assert http.send(raw)[0]==b'result' and len(seen)==1


@pytest.mark.parametrize('media',[None,'text/html','text/event-stream'])
def test_bad_response_media_type_is_not_guessed_json(media):
    def send(request):return httpx.Response(200,headers={'content-type':media} if media else {},content=b'{}')
    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        with pytest.raises(ProviderHTTPError,match='media type'):ParentProviderHTTP(client,'https://fixture.invalid',{}).send(b'{"stream":false}')


@pytest.mark.parametrize('status',[302,401,415,429,500])
def test_refusal_preserves_safe_http_reason_and_code_not_raw_vendor_text(status):
    seen=[]
    def send(request):
        seen.append(request)
        return httpx.Response(status,headers={'location':'https://foreign.invalid'},json={'error':{'code':'invalid_request_error','message':'private prompt Bearer secret-value'}})
    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        with pytest.raises(ProviderHTTPError) as failure:ParentProviderHTTP(client,'https://fixture.invalid',{}).send(b'{}')
        assert str(status) in str(failure.value) and 'invalid_request_error' in str(failure.value)
        assert 'private prompt' not in str(failure.value) and 'secret-value' not in str(failure.value)
        assert len(seen)==1
