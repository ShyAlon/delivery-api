# 2026-10-27 - Shipped JS needs a module-goal parse gate

PR #261 ("startup preflight report") shipped one stray `}` at the end of
identity.js line 75 (the `.then(live).catch(()=>{})` line closed the arrow body
one brace early; the real arrow close then landed inside the `.then(` paren:
"missing ) after argument list"). Every page load died at boot - the SPA sat on
"Loading workspace…" forever. The Python preflight gates parse Python only, and
the already-red CI test job masked the whole playwright suite going red, so the
breakage reached a preview build and Shy's browser.

Detection traps, all three real:
- `node --check file.js` may silently parse in SCRIPT goal and pass a broken
  module. Force module goal with a real copy named `.mjs`.
- A symlink named `.mjs` does NOT work: node resolves the symlink and picks the
  goal from the real `.js` path again.
- V8 version differences are real: only a forced-module-goal check is stable.

Gates now: pytest `test_every_shipped_js_asset_parses_as_an_es_module`
(test_ui_bundle.py), preview-exec step 2b (node-guarded, loud skip without
node), and full-smoke.py (served-bytes-whole for every asset always; syntax
parse when the image has node, loud note otherwise).
