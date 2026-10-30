"""Install explicit operator reads, never infer authority from the admin badge."""
from .tenancy import TenantAccessDenied

SETTINGS=('REQUI_OPERATOR_SUBJECT','REQUI_OPERATOR_REGISTRY_VERSION','REQUI_OPERATOR_CAPABILITIES')

def install(application,environ,*,dsn,enabled):
    # Clear stale/test-injected authority on every lifecycle, including bad config.
    application.state.operator_read_store=None
    application.state.operator_read_status='disabled: tenant runtime required'
    if not enabled:return
    from .operator_authorization import from_environment
    from .operator_reads import OperatorReadStore
    try:principal=from_environment(environ)
    except TenantAccessDenied:
        application.state.operator_read_status='disabled: explicit operator subject, registry version and capabilities are missing or invalid'
        return
    application.state.operator_read_store=OperatorReadStore(dsn,principal)
    application.state.operator_read_status='configured: every read still requires active session and matching enabled database registry'
