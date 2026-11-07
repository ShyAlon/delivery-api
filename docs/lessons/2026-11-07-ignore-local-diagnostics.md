# 2026-11-07 - Ignore ad-hoc local diagnostics

Owner-machine scratch files (grep.txt, requi-*.sh/txt/py) showed up as
untracked noise in `req status`. Root-anchored ignore rules keep them out of
status without touching tracked sources.
