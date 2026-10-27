# 2026-10-27: One current phase signal, optional history

The normalized progress protocol already carries phase, stage, label, status,
counts and timestamps. Changing that protocol would add risk without helping
this UI request. Select a phase-scoped latest signal and replace the subline
under the active phase. Authoritative waiting/error state wins over a delayed
working heartbeat. Do not estimate progress percentages from silence.

History belongs in a collapsed-by-default native details element. Keep current
signal, connection state, elapsed time and signal age visible in its summary.
Capture expansion before replacing a subtree, and remember it by task for
snapshot re-renders. Ignore delayed toggle events from detached old elements.
A DOM clock updates elapsed/age without another network request.

Failing-first browser coverage must check real visibility, stream replacement,
connection loss, expansion retention across stream and snapshot renders,
waiting state, and both narrow and wide layouts. Existing history typography
assertions still run after explicitly opening the newly collapsed history.
