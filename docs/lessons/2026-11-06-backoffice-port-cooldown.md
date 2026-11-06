# 2026-11-06: A bind probe without SO_REUSEADDR turns a cool-down into a hard failure

Problem: the preview's private Back office probe bound its port with a plain socket and failed immediately on any OSError. A predecessor's cooling-down connections (TIME_WAIT) or a mid-shutdown listener produced "[FAIL] Back office cannot own its private port" with no listener actually present, and the message named no holder, leaving the owner to guess.

Lesson: port-ownership probes must bind the way the real server binds (SO_REUSEADDR), retry across the short shutdown window, and when they still refuse, say who holds the port (lsof) or that nothing does. A refusal without a named holder is an instruction to debug blind; name the evidence in the error.
