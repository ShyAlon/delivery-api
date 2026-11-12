# Decision actions hidden by independent scroll owners

The task and gate could be ready while the action was below a long result. Sticky navigation, sticky headings, scrolling controls and a fixed status footer competed for space. A waiting-state halo also looked like a failure.

## Change

Shared Page owns document scrolling and a measured current-action strip. Its ResizeObserver reserves the actual strip height, including wrapped pending/error text. Short viewports (500px or less) use the approved in-document fallback. Headings and private tabs/status footer stay in normal flow. Table retains horizontal overflow without a vertical height cap. Shared Dialog locks the background and scrolls once.

Shared decision infrastructure moves original controls, not clones. Secondary editing, rework, comment and model controls stay closed until opened. It does not change form payloads, validation, enabled-state authority or server actions. Explanation sections start closed so generated evidence is the main content. Ready/waiting information is neutral; missing artifacts and failed submissions remain warnings.

## Proof

A new gate viewport regression fails on the unchanged main branch. Local browser tests cover ready and blocked gates, real deferred pending/failure responses, Enter submission, unchanged payloads, closed secondary fields, dynamic strip reservation, phone/desktop/short views, historical/deep-link navigation, task actions, private tabs/footer and a long native dialog. Existing decision-page tests changed only the intentionally replaced sticky contracts. Local fake routes and isolated Postgres fixtures never call a model or mutate a live workspace.

Do not equate a missing-artifact screenshot with a ready approval test. Inspect pixels as well as enabled-state and overlap assertions. Hosted CI is unverified; the exact-head local gate is the accepted evidence.
