# 2026-10-26 - a local model on the operator's machine taxes the whole machine, build and run alike

The ollama preview mode loads a 14B model on the operator Mac to ground its
pin (pin-local.py POSTs /api/generate with keep_alive) and then runs every
interview turn on it; both halt the machine. The DeepSeek-only mode starts
no Ollama dependency at all: no server check, no model load, no Ollama
setting in the container's settings (guarded by OLLAMA-LEAK), and the paid
key comes from the shell environment or a hidden local prompt, never disk.
With no PREVIEW_EXEC_PROVIDER the script asks 1) ollama 2) deepseek at the
terminal - no default, invalid input re-asked, non-interactive fails fast.
Prevention: a provider mode must enumerate its dependencies and refuse to
start when one leaks in from another mode; "heavier than expected" is a
mode-design problem, not a tuning problem.
