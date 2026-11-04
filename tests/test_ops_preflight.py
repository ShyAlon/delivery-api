from delivery_api.ops_preflight import preflight


def config():
    return {'DELIVERY_DATABASE_URL':'postgresql://user:fixture-password-XYZ@host/db','DELIVERY_PUBLIC_ORIGIN':'https://example.invalid',
        'DELIVERY_SUPABASE_URL':'https://auth.invalid','DELIVERY_SUPABASE_ANON_KEY':'public',
        'DELIVERY_ADMIN_TOKEN':'private-test','DELIVERY_CONTROL_PLANE_ONLY':'1'}


def test_valid_offline_config_is_not_readiness_or_grant_or_secret_dump():
    result=preflight(config())
    assert result['configuration_valid'] and not result['production_ready'] and not result['launch_authorized']
    assert 'fixture-password-XYZ' not in str(result) and 'private-test' not in str(result)


def test_unsafe_fallback_token_origin_provider_key_and_default_profile_fail():
    for changes,key in [({'DELIVERY_DATABASE_URL':'sqlite:///shared.db'},'postgres_configured'),
        ({'DELIVERY_ADMIN_TOKEN':'dev-admin-token'},'nondefault_admin_token'),
        ({'DELIVERY_PUBLIC_ORIGIN':'http://example.invalid'},'https_public_origin'),
        ({'DEEPSEEK_API_KEY':'private'},'provider_secrets_absent'),
        ({'REQUI_OPERATOR_SUBJECT':'unverified'},'no_unreviewed_operator_identity')]:
        result=preflight(config()|changes)
        assert not result['checks'][key] and not result['configuration_valid']


def test_malformed_secret_bearing_urls_fail_without_exception_or_values():
    for key in ('DELIVERY_DATABASE_URL','DELIVERY_PUBLIC_ORIGIN','DELIVERY_SUPABASE_URL'):
        result=preflight(config()|{key:'https://[broken:fixture-private'})
        assert not result['configuration_valid'] and 'fixture-private' not in str(result)
    assert not preflight(config()|{'DELIVERY_SUPABASE_ADMIN_SUBJECTS':'unverified'})['configuration_valid']
