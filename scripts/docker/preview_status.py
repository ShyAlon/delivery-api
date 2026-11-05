"""Health and admission are separate facts. Never infer admission from logs."""

def summarize(health, routes=None):
    if not isinstance(health,dict) or health.get('ok') is not True:
        return {'service':'UNVERIFIED','execution':'UNVERIFIED','message':'Service health is unverified. No work is approved.'}
    if not isinstance(routes,dict):
        return {'service':'HEALTHY','execution':'UNVERIFIED','message':'Health passed; signed route admission is unverified. Inspect Model routes before Start.'}
    entries=routes.get('routes')
    if not isinstance(entries,list) or not entries:
        return {'service':'HEALTHY','execution':'UNVERIFIED','message':'Route response is incomplete. No admission claim is made.'}
    available=[entry for entry in entries if isinstance(entry,dict) and entry.get('available') is True]
    return {'service':'HEALTHY','execution':'AVAILABLE' if available else 'UNAVAILABLE',
            'message':'Runtime route readiness is not permission to start paid work.'}
