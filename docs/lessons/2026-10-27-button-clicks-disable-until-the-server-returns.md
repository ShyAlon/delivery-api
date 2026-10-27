# 2026-10-27 - Server-call buttons: click-level proof of the disable guard

The shared in-flight guard (`runAction`, web/ui-action.js) was already unit-tested
at the guard level; what was missing was click-level proof through the real page
handlers. Three representative buttons now have browser tests (playwright,
hold-the-route pattern): Create empty task (dashboard form submit), Start
interview (#startengine), Confirm rerun (nested dialog). Each clicks, fires an
immediate second click, and asserts: disabled + busy label + aria-busy + title
reason while the POST is held, and exactly ONE request reached the server.

Test-authoring traps, both real:
- Locate by #id, never by accessible name: runAction's busy label changes the
  name mid-test, so a name-based locator loses the element it just clicked.
- The second click must be `el.evaluate('e=>e.click()')`: playwright's
  locator.click() refuses a disabled button (actionability), while the DOM
  .click() on a disabled button is a spec-level no-op - exactly what a fast
  second user click degrades to.
