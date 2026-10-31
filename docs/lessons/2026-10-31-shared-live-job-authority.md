# Shared live job authority

C02: worker claim, provider ledger admission and child broker now use one fresh owner/Auth-session/job/session/generation predicate. Keep caller-specific state and lease/model selection checks separate. A persisted Stop rejects shared job authority even before a generation change is observed. The ledger still rechecks after acquiring the owner lock; this is not a request-time cache or permission to settle accepted work.
