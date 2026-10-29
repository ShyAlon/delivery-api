# Sign task state, classification and parked-item receivers

Three scoped PG receivers assumed unsignedHTTP:task three-state/phase progress,classification-shadow joins to answers,and parked items.json validation/view. The held PG-first default rejects these requests before their state/content assertions.

Use existing signed pg_task_app and owner headers,retaining explicit local compatibility config after fixture construction. All original phase/progress/artifact totals,classification evidence differences,valid ItemSpec[] projection and invalid-item rejection assertions remain unchanged. No production/default-bootstrap change,middleware removal,coverage deletion or assertion weakening.

Measured Python3.11.17:
- Future bootstrap red3failed0.74s.
- Today final3passed1.02s.
- Future final3passed0.84s;restored current conftest afterward.
- Expanded API/tasks/cloud/bootstrap157passed21.26s.
- Full suite/hostedCI unverified. Remaining runner/session and other HTTP migrations precede the final bootstrap flip.

Lesson:a state/content receiver must authenticate its owner request independently of how its PG fixture writes data. Preserve the original compatibility contract without relying on the globally permissive import default.
