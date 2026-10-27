# 2026-10-27 - preview-exec: fail-fast step order, one yes, no key prompt, real defaults

## Why

Two preflight regressions shipped this week through checks that only ran late
(the image smoke after a full build) or through seams their tests mocked. The
launcher also asked three questions (provider, a hidden key prompt, the build's
own yes) and quietly defaulted the local model to qwen2.5-coder:14b although the
operator's model is phi4.

## The shape

8 numbered steps, cheapest and most likely first: inputs/env, source smoke,
confirmation, settings+grounding, build, image smoke, database+startability,
start+readiness. The plan with expected durations prints before step 1; every
step prints its elapsed time; every abort names its step. The source-smoke step
runs what the image smoke would check, minutes before the image exists: every
shipped Python file ast-parses, the startup preflight's own test file runs, and
the candidate engine's surface (create_app, SESSIONS_DSN_ENV, build_pool
max_size) is verified by ast - by ast because the Mac's delivery venv does not
carry the engine's dependencies, so a real import would false-fail on the host.

## Rules

- One typed yes per preview run; the build script takes REQUI_BUILD_YES=1 from
  its caller and keeps its own yes only when run standalone.
- No key prompts anywhere. A key typed into a terminal can end up in
  scrollback; a missing DeepSeek key is a loud NO-KEY before anything is built.
- A launcher that builds its own container env must pin REQUI_EXPECTED_REPOS
  itself; pinning preview.sh's path (#263) did not cover preview-exec's
  build_exec_env, and the staleness check kept skipping there.
- Hermetic launcher tests must exercise the real script end to end (fake
  Ollama server + docker stub), not only early-abort slices: the abort-path
  tests all stayed green while the happy path had never run.
