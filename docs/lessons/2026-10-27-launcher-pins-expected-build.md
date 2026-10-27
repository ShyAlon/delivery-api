# The launcher must pin the build it expects, or staleness is undetectable

2026-10-27: the startup preflight's build-staleness check compares the running
image's REQUI_BUILD_REPOS against REQUI_EXPECTED_REPOS - but nothing set the
expectation, because scripts/ops/preview.sh was frozen by a git-diff-clean
test ("must stay exactly as it is"). A frozen launcher made the check a
permanent skip on the exact path where stale-image confusion burns hours.
Unfreezing meant replacing the blanket freeze with a precise invariant: both
preview paths (local settings and server settings) must pin
REQUI_EXPECTED_REPOS after their build_env call. Lesson: freeze tests should
name WHAT must not change; "the whole file" silently forbids the next fix.
