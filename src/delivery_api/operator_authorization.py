"""Explicit sole-operator authorization. No effect or identity inferred from admin."""
from dataclasses import dataclass
from uuid import UUID
from .tenancy import current_scope,TenantAccessDenied

CAPABILITIES=frozenset({'usage.read','analytics.read','sessions.read','debug.read','logs.read',
    'credits.allocate','users.suspend','messages.prepare','sessions.operate','services.operate'})

@dataclass(frozen=True)
class OperatorPrincipal:
    subject:str
    registry_version:str
    capabilities:frozenset

    def __post_init__(self):
        try:canonical=str(UUID(self.subject))
        except (ValueError,TypeError,AttributeError) as exc:raise ValueError('verified operator subject required') from exc
        if canonical!=self.subject or not self.registry_version or not isinstance(self.registry_version,str):
            raise ValueError('canonical subject and registry version required')
        if not isinstance(self.capabilities,frozenset) or not self.capabilities or not self.capabilities<=CAPABILITIES:
            raise ValueError('explicit reviewed operator capabilities required')

    def authorize(self,store,capability):
        scope=current_scope()
        if scope.owner_id!=self.subject or capability not in self.capabilities:
            raise TenantAccessDenied('operator principal or capability denied')
        if not scope.session_id or store.auth_session_active() is not True:
            raise TenantAccessDenied('active verified operator session required')
        return {'operator_subject':self.subject,'session_id':scope.session_id,
            'registry_version':self.registry_version,'capability':capability}


def from_environment(environ):
    """Missing config stays disabled; deployment must bind owner's verified subject."""
    import json
    try:
        subject=environ['REQUI_OPERATOR_SUBJECT'];version=environ['REQUI_OPERATOR_REGISTRY_VERSION']
        caps=json.loads(environ['REQUI_OPERATOR_CAPABILITIES'])
        if not isinstance(caps,list) or any(not isinstance(c,str) for c in caps) or len(set(caps))!=len(caps):raise ValueError()
        return OperatorPrincipal(subject,version,frozenset(caps))
    except (KeyError,ValueError,TypeError) as exc:
        raise TenantAccessDenied('reviewed sole-operator registry unavailable') from exc
