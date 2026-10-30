"""Explicit local-only selector and aggregate connection budget.

Default delivery nine + compatibility four + supervisor one = fourteen. Opt-in removes compatibility
service and caps parent+one active child at 8+4=12, leaving 3 of 15 spare.
"""
def child_mode(env):
    mode=env.get('DELIVERY_CHILD_EXECUTION','').strip()
    if not mode:return False
    if mode!='local':raise ValueError('child execution accepts local only; paid subprocess remains refused')
    if env.get('DELIVERY_LOCAL_PREVIEW')!='1':raise ValueError('child execution requires explicit local preview')
    return True


def pool_budget(env):return (8,4,0) if child_mode(env) else (9,0,4)
