# Preview must respect execution capability

A tenant stage rerun offered an archive preview before the endpoint refused the
confirmed action because new-generation session/checkpoint recovery is absent.
The preview was technically read-only but made an unavailable operation look
ready to execute. Validate this capability before preview, publish it on task
reads, and explain the limitation visibly rather than silently removing a button.
Keep supported failed-generator retries separate from generation reruns. Tests
must cover both preview and confirmation refusal, unchanged local rerun behavior,
and visible tenant recovery guidance with supported retry buttons preserved.
