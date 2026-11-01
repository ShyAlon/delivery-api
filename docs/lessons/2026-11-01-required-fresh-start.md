# Component green is not a working first run

Local-ledger tests seeded positive credits; browser fixtures disabled lifespan; child tests used fake gateways; normal PR selection excluded cross-repo integrations. Those layers left startup, schema/grants, zero-credit local admission and hidden-name reuse disconnected.

Fresh-start acceptance migrates owned disposable PG, executes real readiness and full-start preflight with restricted role, enables real application lifespan and production local gateway, creates/deletes/recreates via signed-auth HTTP, starts the real interview child and reaches its first completed stage with settled zero-cost usage. Only remote identity-key retrieval and local provider responses are replaced. No live account or paid endpoint is used.

Named CI job pins engine/generator sources, fails without source access, and refuses skips or missing scenario results. Re-run on synchronize as well as PR creation. Image publication depends on this job. Repository branch protection must separately require fresh-start-acceptance; workflow YAML cannot itself enforce merge settings. Missing schema fails readiness, incorrect grants-map fails full preflight, name reuse fails recreate, zero-credit denial fails completed-stage assertion. Producer source pins must be updated alongside the release tuple.
