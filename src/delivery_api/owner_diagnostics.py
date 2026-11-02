"""Safe names/booleans for owner commands, never raw exception text or values."""
import re

# Fixed messages from our validators. Prefixes deliberately discard paths,
# setting values and source content that may follow the known reason.
_REASONS={
 'configured identity differs':'configured_identity_mismatch',
 'local authority not covered':'local_authority_not_covered',
 'unknown registry capability':'unknown_registry_capability',
 'exactly one enabled registry row required':'sole_enabled_operator_required',
 'invalid or incomplete registry':'invalid_registry_configuration',
 'target mismatch':'database_target_mismatch',
 'targets differ':'database_target_mismatch',
 'both DSNs required':'database_dsn_missing',
 'not confirmed':'confirmation_cancelled',
 'settings changed':'settings_changed',
 'registry changed':'registry_changed',
 'target identity conflict':'target_identity_conflict',
 'existing authority identity/version conflict':'existing_identity_version_conflict',
 'existing local authority revoked or unverifiable':'local_authority_revoked_or_unverified',
 'explicit settings file is missing:':'explicit_settings_file_missing',
 'settings file must not be a symlink:':'settings_symlink_refused',
 'settings file must be private (chmod 600):':'settings_permissions',
 'cannot read settings (':'settings_unreadable',
 'invalid settings syntax at line ':'settings_syntax',
 'duplicate setting name:':'duplicate_setting_name',
 'project settings conflict for ':'settings_identity_conflict',
}


def failure(report,exc,stage):
    """Preserve the failed stage, exception class and fixed reason, not secrets.

    Unrecognized exception messages stay hidden. SQLSTATE is a bounded database
    class code, not SQL, role names, DSNs, server detail or arbitrary text.
    """
    stage=getattr(exc,'owner_diagnostic_stage',stage)
    if not re.fullmatch(r'[a-z][a-z0-9_.]*',stage):raise ValueError('fixed diagnostic stage required')
    report['failure.stage.'+stage]=True
    name=type(exc).__name__
    report['failure.exception.'+(name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}',name) else 'Unknown')]=True
    text=str(exc)
    reason=next((code for prefix,code in _REASONS.items() if text==prefix or text.startswith(prefix)),None)
    if any(x in text.lower() for x in ('unsupported startup parameter', 'unsupported startup option', 'unsupported startup packet parameter')):reason='startup_parameter_unsupported'
    if isinstance(exc,KeyError):reason='required_name_missing'
    if isinstance(exc,KeyboardInterrupt):reason='interrupted'
    report['failure.reason.'+(reason or 'exception_during_named_stage')]=True
    sqlstate=getattr(exc,'sqlstate',None)
    if isinstance(sqlstate,str) and re.fullmatch(r'[0-9A-Z]{5}',sqlstate):report['failure.sqlstate.'+sqlstate]=True
