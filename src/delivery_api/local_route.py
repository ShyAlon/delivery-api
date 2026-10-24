"""The operator's local execution preview adds exactly one managed local Ollama route.

The flag is forced by scripts/ops/preview-exec.sh and is absent on the deployed
server, so deployed cloud keeps exactly the managed DeepSeek route. Nothing here
enables execution; the admission catalog still refuses any call without a
reviewed price row for the selected model.
"""
import os

LOCAL_PREVIEW_ENV='DELIVERY_LOCAL_PREVIEW'


def local_preview_enabled(environ=None):
    environ=os.environ if environ is None else environ
    return environ.get(LOCAL_PREVIEW_ENV)=='1'


def managed_routes(environ=None):
    """Cloud-tenants managed routes, in offer order. Deployed cloud: DeepSeek only."""
    routes=['deepseek']
    if local_preview_enabled(environ):routes.append('ollama')
    return routes
