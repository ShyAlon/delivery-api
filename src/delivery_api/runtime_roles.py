"""Reviewed application-login SET memberships, never wildcard role discovery.

These roles grant no human operator capability and do not activate payments.
Service operations still check the current auth session, tenant/RLS and registry.
"""
RUNTIME_ROLES=(
    'requi_backend','requi_operator_read','requi_operator_usage',
    'requi_operator_payment_read','requi_operator_grant','requi_metrics_writer',
    'requi_log_writer','requi_engine_config_read','requi_model_pin_editor','requi_payment',
)
