"""An explicit delivery-only runtime profile never enables execution."""
import os


def control_plane_only():
    value=os.environ.get('DELIVERY_CONTROL_PLANE_ONLY','0')
    if value not in ('0','1'):raise RuntimeError('DELIVERY_CONTROL_PLANE_ONLY must be 0 or 1')
    return value=='1'


def require_engine_execution():
    if control_plane_only():raise RuntimeError('engine execution is unavailable in the delivery-only runtime')
