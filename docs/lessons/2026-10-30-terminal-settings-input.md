# Test hidden input against a real terminal, not just injected prompt callbacks

The owner ran from a real Mac terminal but saw "hidden input requires a terminal".
The loader used open('/dev/tty','r+'), a buffered text read/write mode that needs
seekability. A terminal is non-seekable. Python raised UnsupportedOperation before
the isatty check, and our generic OSError handler blamed the terminal. Optional
save confirmation had the same bug. A real local PTY reproduced it immediately.

Both now use one raw O_RDWR descriptor, explicit TTY validation and termios echo
disabling/restoration for secrets. No echoed-stdin fallback exists. The PTY test
enters a secret and yes, proves both complete and no secret appears in terminal,
stdout or stderr. This affects every shared settings prompt.

A second report said the admin URL was already in .env. The per-key loader already
supports repo.env fallback despite app credentials in preview.env, including
export, spaces and quotes. PREVIEW_SETTINGS_FILE changes the first path, not the
per-key fallback; explicit --env-file alone selects a single file. No source was
proved wrong on the owner's Mac. Diagnostics now show exact searched paths and
name absent/present-but-empty/present, never values. The missing-setting error
prints that context before prompting. Do not ask users to retype credentials or
move files until their selected-source evidence establishes why a name is missing.

Two regressions failed first. Real PTY, loader, identity and disposable migration
checks pass. No live DB query/write, owner-machine command, credential value read,
Mac terminal execution, Docker, deploy, spend or hosted CI was performed.
