"""Offline rollout config inspection. No connection, credential or launch proof."""
from urllib.parse import urlsplit


def parse_url(value):
    try:
        parsed=urlsplit(value)
        _=parsed.hostname,parsed.port
        return parsed
    except (ValueError,TypeError):
        return urlsplit('')


def preflight(environ):
    checks={}
    database=environ.get('DELIVERY_DATABASE_URL','')
    parsed=parse_url(database)
    checks['postgres_configured']=parsed.scheme in ('postgres','postgresql') and bool(parsed.hostname) and bool(parsed.path.strip('/'))
    origin=parse_url(environ.get('DELIVERY_PUBLIC_ORIGIN',''))
    checks['https_public_origin']=origin.scheme=='https' and bool(origin.hostname) and not origin.username and not origin.password and not origin.query and not origin.fragment and origin.path in ('','/')
    auth=parse_url(environ.get('DELIVERY_SUPABASE_URL',''))
    checks['supabase_auth_configured']=auth.scheme=='https' and bool(auth.hostname) and not auth.username and not auth.password and bool(environ.get('DELIVERY_SUPABASE_ANON_KEY'))
    checks['no_turso_fallback']=not environ.get('TURSO_DATABASE_URL') and not environ.get('TURSO_AUTH_TOKEN')
    checks['no_local_auth_fallback']=environ.get('DELIVERY_BASIC_AUTH_ENABLED','0')=='0' and not any(environ.get(k) for k in ('DELIVERY_GOOGLE_CLIENT_SECRET','DELIVERY_GITHUB_CLIENT_SECRET'))
    checks['secure_cookie']=checks['https_public_origin']
    checks['nondefault_admin_token']=bool(environ.get('DELIVERY_ADMIN_TOKEN')) and environ.get('DELIVERY_ADMIN_TOKEN')!='dev-admin-token'
    checks['provider_secrets_absent']=not any(environ.get(k) for k in ('OPENAI_API_KEY','DEEPSEEK_API_KEY','ANTHROPIC_API_KEY','SUPABASE_SERVICE_ROLE_KEY','PAYPAL_CLIENT_SECRET'))
    checks['no_unreviewed_operator_identity']=not environ.get('REQUI_OPERATOR_SUBJECT') and not environ.get('DELIVERY_SUPABASE_ADMIN_SUBJECTS')
    return {'scope':'offline_config_only','checks':checks,'configuration_valid':all(checks.values()),
        'production_ready':False,'launch_authorized':False,'network_checked':False,
        'unverified':['restricted database role/RLS/current schema','Supabase JWKS and active-session revocation',
            'host TLS/origin/loopback publication','backup age/integrity/cloud restore drill and artifacts',
            'expense policy/prices/outbox delivery','payment account/sandbox signature and reconciliation',
            'full clean run and owner launch scope']}
