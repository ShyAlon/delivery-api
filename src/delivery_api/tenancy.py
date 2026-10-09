"""Tenant capabilities and filesystem containment, independent of authentication.

Only authentication may mint request scope. Workers capture a scope at dispatch,
then restore it for their own execution. An absent scope never means administrator.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
import re


class TenantAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class TenantScope:
    owner_id: str
    session_id: str | None = None

    def __post_init__(self):
        # Supabase Auth subjects are UUIDs. Canonicalize, don't permit path aliases.
        try:
            owner = str(UUID(self.owner_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise TenantAccessDenied('invalid tenant identity') from exc
        object.__setattr__(self, 'owner_id', owner)
        if self.session_id is not None:
            try:object.__setattr__(self,'session_id',str(UUID(self.session_id)))
            except (ValueError,TypeError,AttributeError) as exc:raise TenantAccessDenied('invalid auth session') from exc


_scope: ContextVar[TenantScope | None] = ContextVar('delivery_tenant', default=None)


def current_scope() -> TenantScope:
    scope = _scope.get()
    if scope is None:
        raise TenantAccessDenied('tenant identity required')
    return scope


@contextmanager
def tenant_context(scope: TenantScope):
    if not isinstance(scope, TenantScope):
        raise TenantAccessDenied('verified tenant scope required')
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def session_root(root: Path, scope: TenantScope, session_id: str) -> Path:
    """Return the only allowed persistent worker directory for this capability.

    Binding session_id to an authorized task is the repository's responsibility.
    Containment is checked even when paths do not yet exist, and again on reads.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', session_id):
        raise TenantAccessDenied('invalid session identity')
    base = root.resolve()
    tenant = base / 'tenants' / scope.owner_id
    expected = tenant / 'sessions' / session_id
    resolved = expected.resolve()
    if resolved != expected:
        raise TenantAccessDenied('session namespace contains a symlink')
    return resolved


def contained_file(root: Path, relative: str) -> Path:
    """Resolve an artifact only under an already-authorized session root."""
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise TenantAccessDenied('invalid artifact path')
    base = root.resolve()
    candidate = (base / path).resolve()
    if not candidate.is_relative_to(base):
        raise TenantAccessDenied('artifact escapes authorized session')
    return candidate
