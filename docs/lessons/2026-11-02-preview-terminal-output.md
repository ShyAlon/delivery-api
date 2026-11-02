# Severity at the call site, one visible launcher run

Do not infer severity from message words. The stream-wide regex colored ordinary plan text containing "error" or "failure". Replace it with shared error/warn/prompt emitters that color only when their output is a TTY and NO_COLOR is unset. Child output keeps its own formatting and severity; no heuristic classifier sits between it and the terminal.

The filter also waited for a complete line, swallowing the newline-free confirmation prompt until after the user answered. Prompts now write a complete cyan "Waiting for input" line directly before read, without an intermediate buffering process. A PTY regression must observe the prompt before supplying an answer.

Settings preparation re-executes the launcher with private values in its environment. That handoff used to happen after the identity, plan, step 1 and container checks, duplicating output and potentially repeating side effects. Complete settings handoff before the visible run starts. Real launcher/settings regression counts one identity, one nine-step plan and one step-1 header. File-first secret handling, startup checks, confirmations and exit codes remain unchanged.

Readiness is evidence, not a hardcoded banner. DELIVERY_CLOUD_EXECUTION=1 plus a key count proves requested configuration, not admitted gateways. Use the latest explicit application startup OPEN/CLOSED status; missing status is UNVERIFIED. Report that once at the final summary, yellow for closed/unverified. The user can repair pins through the available UI without a false execution-open claim. Also include shared terminal helpers in hermetic deploy fixture copies, not just preview fixtures.
