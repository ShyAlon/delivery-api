# Feedback load failure is not no feedback

Keep last-known feedback per task, separately track availability and show a scoped
read-only retry notice. Disable thumbs/comments and guard editing functions while
unavailable so unknown state cannot overwrite a prior comment. Retry reads only;
never retry a mutation automatically. A successful read refreshes note/action rows.
Browser regression failed before the fix and covers recovery without reopening.
