"""Command line entry: serves the web UI and the server APIs on one uvicorn listener (python -m delivery_api)."""


def main():
    import os
    import uvicorn
    from .networking import delivery_listener
    host,port=delivery_listener(os.environ)
    print(f'Web and server APIs: http://{host}:{port} (same listener)')
    # No uvicorn access log: the request_log middleware records every request with
    # richer fields (actor, duration, DB cost); routine successes go to the file
    # log, non-2xx/slow/errors also to the console. Access lines would double-log.
    uvicorn.run('delivery_api.app:app',host=host,port=port,reload=False,access_log=False)


if __name__ == '__main__':
    main()
