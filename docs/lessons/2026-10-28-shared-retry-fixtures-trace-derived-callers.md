# Trace derived callers when migrating shared retry fixtures

The failed-Needs helper is reused by failed-architecture tests. Port both helper
layers and all callers together. Normal phase updates already establish failed
architecture state,so assert that state instead of forcing it with backend SQL.
Keep preview/confirm,old failure,duplicate/artifact/model refusal,generation,
cost and failed-launch assertions unchanged. Run the full mixed module to catch
helper callers missed by a direct-reference scan.
