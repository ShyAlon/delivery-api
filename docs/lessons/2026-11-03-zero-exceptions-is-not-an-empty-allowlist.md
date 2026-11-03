# Zero exceptions is not an empty allowlist

After the three component themes, the recorded inventory was empty but the original checker still permitted new legacy allowances and missed programmatic Page/Dialog creation, nested asset folders, and JS-injected structural CSS.

The gate now rejects any nonempty allowance file. It checks recursively, with one exact owner for each component's construction and structural styles; one owner cannot construct another component. Fixtures fail for native markup, createElement/createElementNS, h/el calls, dialog roles, shell class assignment, injected styles and nested inline media rules. Legitimate content classes and DOM queries stay allowed. Vendored framework sources are outside first-party scope.

The stronger check found three purchase-specific header/main rules still in purchase.css. Those rules moved unchanged to Page at the same stylesheet anchor; no page-specific shell-style exception was added. Real purchase-shell and state tests remain part of validation, with all payment responses local fixtures and no real payment or provider execution.

This remains a static source gate, not a proof system for arbitrary JavaScript. Runtime-generated or intentionally obfuscated selectors can evade static matching. Do not claim those are verified. The gate is an explicit CI step and tests pin its coverage and cross-component owner isolation.
