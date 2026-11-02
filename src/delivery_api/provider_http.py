"""Parent-owned HTTP envelope for exact-byte child provider requests.

The SDK body crosses the private broker; its headers and credentials do not.
Reconstruct the supported protocol here, never from child-supplied headers.
"""
import json
import httpx

MAX_RESPONSE=8*1024*1024


class ProviderHTTPError(RuntimeError):
    def __init__(self,message,*,status=None,code=None):
        super().__init__(message);self.status=status;self.code=code


class ParentProviderHTTP:
    def __init__(self,client,endpoint,headers):
        url=httpx.URL(endpoint)
        if url.scheme not in ('http','https') or url.username or url.password or url.query or url.fragment:
            raise ValueError('fixed reviewed provider endpoint required')
        if client.follow_redirects:raise ValueError('provider redirects forbidden')
        self.client,self.endpoint,self.headers=client,endpoint,dict(headers)

    def send(self,raw):
        # Do not use json=: accounting, replay and dispatch bind these exact bytes.
        payload=json.loads(raw);stream=payload.get('stream',False)
        headers=httpx.Headers(self.headers)
        headers['Content-Type']='application/json'
        headers['Accept']='text/event-stream' if stream else 'application/json'
        request=self.client.build_request('POST',self.endpoint,content=raw,headers=headers)
        response=self.client.send(request,stream=True,follow_redirects=False)
        try:
            body=bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body)>MAX_RESPONSE:raise ProviderHTTPError('Provider response exceeded the supported size; review this attempt before retrying.')
            body=bytes(body)
            if response.status_code!=200:
                code=safe_error_code(body)
                # Arbitrary vendor messages can echo project text or credentials.
                # Fixed HTTP reason + reviewed code preserves useful safe text.
                reason=httpx.codes.get_reason_phrase(response.status_code) or 'Request rejected'
                raise ProviderHTTPError(f'Provider returned HTTP {response.status_code} {reason}'+(f' ({code})' if code else '')+'. Review the failed attempt before retrying.',status=response.status_code,code=code)
            media=response.headers.get('content-type','').split(';',1)[0].strip().lower()
            expected='text/event-stream' if stream else 'application/json'
            if media!=expected:
                raise ProviderHTTPError(f'Provider response media type does not match {expected}; review this attempt before retrying.')
            return body,media
        finally:response.close()


def safe_error_code(body):
    try:
        error=json.loads(body).get('error',{})
        code=error.get('code') if isinstance(error,dict) else None
    except (ValueError,AttributeError,TypeError):return None
    return code if code in ('invalid_api_key','authentication_error','insufficient_balance','invalid_request_error','rate_limit_exceeded','model_not_found','server_error','context_length_exceeded','invalid_model','invalid_request') else None
