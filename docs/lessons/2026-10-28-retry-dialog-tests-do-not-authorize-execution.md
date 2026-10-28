# Retry dialog tests do not authorize execution (2026-10-28)

Eight error/retry browser cases now use signed PG task fixtures with unchanged
assertions. Model,execution and retry-response mocks still intercept confirm
submission,so no model call or paid retry happens. Report browser wording/model
selection/confirmation payload proof separately from real checkpoint recovery.
Legacy Ollama spelling in the case is a compatibility input,not local-auth
activation.

Fixture signature guard failed first. Eight cases plus guard,cloud API and
bootstrap passed47,skipped0 in21.92s. Inspected Needs and Architecture dialogs:
last failure,default model,cost warning and fresh/checkpoint text are readable.
Production unchanged;hosted CI and full suite remain unverified.
