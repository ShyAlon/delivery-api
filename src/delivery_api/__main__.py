"""Command line entry: serves the web UI and the server APIs on one uvicorn listener (python -m delivery_api)."""


def main():
    import os
    import uvicorn
    from .networking import delivery_listener
    host,port=delivery_listener(os.environ)
    print(f'Web and server APIs: http://{host}:{port} (same listener)')
    uvicorn.run('delivery_api.app:app',host=host,port=port,reload=False)


if __name__ == '__main__':
    main()
