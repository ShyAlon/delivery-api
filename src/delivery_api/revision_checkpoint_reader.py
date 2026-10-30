"""Owner-scoped PG source review. A path is scratch context, never authority."""
def read_source_checkpoint(store,capability,path,*,session_id,expected_checkpoint_id,expected_review_phase=None):
    from .generator_checkpoint import GeneratorCheckpoint
    if session_id!=capability.session_id:raise ValueError('source review execution identity differs')
    if expected_review_phase is not None and expected_review_phase not in ('srs','architecture'):raise ValueError('implemented staged review phase required')
    # Source inspection spans graph reconstruction on one locked transaction.
    # No worker context or filesystem claim is needed to READ owned settled work.
    current=store._connection.get()
    from contextlib import nullcontext
    with nullcontext(current) if current is not None else store._postgres.transaction() as c:
        token=store._connection.set(c)
        try:
            c.execute('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(capability.task_id,)).fetchone()
            saver=GeneratorCheckpoint(store,capability,None,'spec',writable=False)
            cfg={'configurable':{'thread_id':session_id}}
            row=saver.get_tuple(cfg)
            if row is None or row.config['configurable']['checkpoint_id']!=expected_checkpoint_id:raise ValueError('reviewed source checkpoint changed')
            from copy import deepcopy
            if expected_review_phase is None:return deepcopy(row.checkpoint['channel_values'])
            from spec_generator.graph import build_graph
            from types import SimpleNamespace
            state=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=saver).get_state(row.config)
            if state.next!=('gate_'+expected_review_phase,):raise ValueError('staged checkpoint must await fresh edited gate')
            return deepcopy(state.values)
        finally:store._connection.reset(token)
