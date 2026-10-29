# Sign detailed-audit receivers before the default PG flip

Three existing task-audit tests use scoped PG storage but unsigned HTTP. Under the held PG-first bootstrap all three fail401 before checking their detailed totals. Add the existing signed pg_task_app and owner headers. Retain explicit local compatibility config after fixture construction:cloud audit intentionally redacts totals to price,while these tests preserve the detailed local token/legacy-scale/cumulative-price contract. Do not reinterpret that contract as cloud support or remove assertions to match cloud redaction.

No production changes,no middleware removal,no assertion changes or weakening,no coverage deleted. The default bootstrap remains on a separate unpublished local branch. This PR is green under today's default and the held future bootstrap for these receivers.

Measured Python3.11.17:
- Future bootstrap before fix:3failed0.59s (unsigned401).
- Signed requests without retaining local compatibility also fail3because cloud redacts token totals;this trial was not committed as the final behavior.
- Current default final3passed0.63s.
- Held future bootstrap final3passed0.59s;restored current conftest afterward.
- Expanded API/tasks/cloud/bootstrap157passed21.20s.
- Full suite and hostedCI unverified. Other mixed receivers still require migration before the final flip.

Lesson:signing a receiver must preserve the original audience's response contract. Keep the explicit compatibility mode where detailed local evidence is the thing being tested;do not weaken totals assertions or silently open cloud disclosures.
