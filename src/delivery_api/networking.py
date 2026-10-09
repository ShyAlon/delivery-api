"""Explicit shared web/API listener configuration. No firewall mutation."""
def delivery_listener(environ):
    host=environ.get('DELIVERY_HOST','127.0.0.1')
    explicit=environ.get('DELIVERY_PORT');legacy=environ.get('PORT')
    if explicit and legacy and explicit!=legacy:raise ValueError('DELIVERY_PORT and PORT conflict')
    value=explicit or legacy or '8000'
    if not isinstance(value,str) or not value.isascii() or not value.isdigit() or not 1<=int(value)<=65535:
        raise ValueError('DELIVERY_PORT must be an integer from 1 to 65535')
    if not isinstance(host,str) or not host or any(c.isspace() for c in host):raise ValueError('explicit delivery bind host required')
    return host,int(value)
