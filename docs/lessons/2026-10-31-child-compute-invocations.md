# A child invocation is not the whole operation

Generator gate continuation or recovery may launch a fresh child under the same job/operation with a later lease epoch. Exact compute segment and durable transcript keys must include that invocation epoch. Different invocations bill their own verified transcript; repeated finish of one invocation remains once-only. Reopening the same segment stays refused, and missing/conflicting evidence still requires reconciliation. No guessed elapsed duration or automatic paid rerun.
