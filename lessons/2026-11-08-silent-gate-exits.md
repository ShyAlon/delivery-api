# A gate that can die silent will, and the empty log becomes the bug report

The preview browser smoke had one exit path - "Chromium binary not installed
for this interpreter" - that wrote zero bytes before exiting 2. The caller
relayed stdout+stderr into the evidence log and reported "browser boot
unverified". Result: a healthy app, an empty log, and a failure message that
named the wrong suspect (the boot) while hiding the remedy (one install
command). The design even knew: the exit-2 contract said "the caller prints
the install hint" - a caller that was never written.

Two rules. Every exit path of a gate must leave evidence: if a branch can
produce an empty log, print the branch's own remedy there, because the
relaying caller cannot know it. And a contract split across two files (one
exits, the other explains) is a contract that does not exist - put the
remedy where the failure is detected, and let the environment repair the one
failure it can: missing browser -> install for that exact interpreter ->
retry the gate once.
