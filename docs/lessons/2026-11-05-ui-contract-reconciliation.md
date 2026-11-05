# A red browser assertion can describe a superseded contract

Four inherited UI failures followed the theme-3 merge. Reproduced all four on unchanged main before editing. Two old assertions contradicted later protections: normal-flow task/panel headings avoid overlapping sticky layers (Nov2 71949a5), and stale founder rationale stays available for copying (Nov1 d372ad9). The owner asked Nov1 12:38:29 to retain the page and data on failed creation rather than force retyping. Reconcile these contracts explicitly; do not silently restore old behavior to make the old gate green.

The stale-dialog browser contract now requires preserved copyable read-only rationale, disabled choices/submit, rejection of programmatic resubmission and Enter, an enabled Close, and a refresh only after explicit Close. The previous implementation disabled Close along with submit and used disabled rather than read-only text. No browser persistence is added.

The task heading stays in flow; navigation stays pinned. Keep the current live-answer status visible below the measured navigation height instead of restoring stacked sticky task/panel titles. Preserve internal history scroll, chronological labels, bounded page height, static panel title and actual unobscured status geometry.

An unavailable session can have a failed control snapshot while the saved task still says running. Show its unavailable-rerun explanation without showing a recovery warning for a healthy running task. Supported generator Retry remains available.

Local gate: the complete 160-case task UI suite passed in four bounded 40-case batches; seven related dialog/form/tenant-rerun cases passed. The first broad 80-case attempt timed out and is not counted. Desktop stale-rationale, scrolled live-status and normal-flow heading pixels inspected. Hosted CI is unavailable due to the owner's reported monthly budget; no CI pass claimed. No live DB, server, deployment, migration or paid run touched.
