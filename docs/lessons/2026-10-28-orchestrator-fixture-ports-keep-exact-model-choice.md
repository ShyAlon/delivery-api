# Orchestrator fixture ports keep exact model choice

Moving durable orchestration state to PG must preserve exact selected models
across stages and switches,not silently fall back to generator environment values.
Keep command bounds,executor ownership and durable route event assertions. Mocked
runner calls remain planning evidence,not proof of provider execution or cloud
admission;state that policy boundary explicitly.
