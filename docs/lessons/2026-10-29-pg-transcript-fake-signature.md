# Fix the fake signature without dropping transcript assertions (2026-10-29)

Owner Q2 approved fixing legacy transcript fake and porting its receiver to PG.
Original test fails1in1.52s:helper now receives task_id,supplemental while lambda
accepts1argument. Fake accepts2;isolated poisoned PG replaces SQLite constructor.
Explicit local compatibility mode preserves legacy unbound checkpoint semantics;
not claimed cloud checkpoint read. Full question/answer present and obsolete
placeholder question absent assertions unchanged. No production change.
Final41passed0skips4.60s transcript+guard/cloud API/bootstrap. Hosted CI/full
suite unrun;no deploy/live DB/model call. Production legacy helper remains.
