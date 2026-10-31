# One exact serialized request contract

C03: the broker rejected duplicate/nonfinite JSON while gateway and context ceiling used last-key-wins parsing. Share the model/output-cap contract across all three before reservation. Preserve exact SDK bytes and digest, replay binding, caps, estimates and dispatch ordering. Six gateway/ceiling ambiguity regressions failed before the fix; broker refusal remains before journal/provider effects.

Quote assembly now uses the already-reviewed catalog directly rather than constructing another catalog and closure for every serialized call. Frozen ledger quotes are unchanged. This is not atomic reserve/start or paid-child cutover completion, and removes no cost/loop protection.
