# A pull cannot replace an already-running wrapper

Owner report: migration review ended at the SQL dump with no visible yes prompt, and colors still followed words rather than severity. The run began on afd7a0b, whose req runner started an outer line-buffered keyword color filter before the helper pulled fixed main. The pull removed the filter file for future runs, not the existing outer process. A raw no-newline printf prompt remained buffered in that process.

Shared prompt input must write a complete, flushed prompt line directly to the controlling terminal before blocking. Explicit prompt severity selects cyan; error/warning calls select red/yellow. No keyword regex. Redirected logs stay free of ANSI; the active prompt belongs to the terminal. Python settings/confirmation prompts follow the same complete-line rule, with secrets still echo-disabled. Do not replace newline prompts with read -p or command substitution.

Track the migration helper in the repository and use the shared prompt/warning calls. A fresh helper must call the checked-out helper after its clean-main sync; do not duplicate prompt implementations in attachments. If an old wrapper is alive, restart via the explicit repository runner, never claim a pull repaired it in place. A pending apply is not a completed migration; repeat the dry inventory before recommending a second apply.
