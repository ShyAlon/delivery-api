# Separate private-function review from migration apply

A generic DELETE keyword gate correctly refused the new cache definitions. Do
not waive that gate for any filename or classify every function DELETE as safe.
Allow only exact reviewed file hashes and the sole DELETE classification, with
an interactive filename-and-SHA phrase before the normal migration-count phrase.
A changed byte, renamed file, DROP or TRUNCATE still refuses before any SQL.

Keep one owner runner: preflight is read-only; pending files apply in order,
each in its own transaction. A failure rolls back that file, leaves earlier
commits intact and stops later work. Rerunning inventory resumes from catalog
state, refusing partial migrations. No live apply or automatic down-migration
belongs in development or tests. Test the full sequence only in disposable PG.
