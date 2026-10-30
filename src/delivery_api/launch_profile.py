"""Read-only launch validation and environment propagation, with no DSN output."""
import os,sys


def engine_environment(env):
    derived=dict(env)
    dsn=derived.get('DELIVERY_DATABASE_URL','')
    if not dsn.startswith(('postgres://','postgresql://')):
        raise ValueError('PostgreSQL database configuration is required')
    token=derived.get('INTERVIEW_ENGINE_TOKEN','')
    if not token or token=='local-interview-engine-token' or len(token)<24:
        raise ValueError('INTERVIEW_ENGINE_TOKEN requires a private value of at least 24 characters')
    derived['INTERVIEW_ENGINE_SESSIONS_DSN']=dsn
    derived['INTERVIEW_ENGINE_TENANTS']='1'
    derived['INTERVIEW_ENGINE_SESSIONS_FILE']='0'
    return derived


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    try:
        from . import config
        # config loads dotenv without overriding exported values. Propagate that
        # exact profile to children, rather than relying on the shell to load it.
        env=dict(os.environ)
        env['DELIVERY_DATABASE_URL']=config.DATABASE_URL
        if argv and argv[0]=='--validate-engine':engine_environment(env)
        elif argv and argv[0]=='--engine':
            if len(argv)<2:raise ValueError('engine command is required')
            os.execvpe(argv[1],argv[1:],engine_environment(env))
        elif argv:raise ValueError('unsupported launch profile option')
    except (RuntimeError,ValueError) as exc:
        print('Error: '+str(exc),file=sys.stderr)
        return 1
    print('PostgreSQL profile configured; database/auth values hidden.')
    return 0


if __name__=='__main__':raise SystemExit(main())
