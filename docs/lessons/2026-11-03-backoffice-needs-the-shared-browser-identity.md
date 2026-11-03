# A private listener is not a signed-in browser

Back office could forward a browser session to the service, but could not create,
renew or end one. Its local missing-header refusal also affected reads, Stop,
model pins and audit, not only Delete. Cookies cross ports on the same hostname,
not localhost/127.0.0.1 boundaries. A CLI acceptance session is not a human browser
session. Do not diagnose a missing operator grant from this401.

Both applications now consume account-component.js for the exact sign-in form
and avatar menu. Main identity keeps its diagnostic callback; Back office points
the same Debug action at its existing evidence tab. The service still owns auth,
sessions and registry checks. Back office has only exact bounded auth routes,
same-origin POST checks and separate HttpOnly Set-Cookie propagation. There is
no token textbox, local credential store, generic proxy, or automatic write replay.

Back office waits for authenticated identity before mounting protected consumers.
A401 hides existing workspace content, closes confirmation dialogs and renders
shared sign-in. Reauthentication reloads fresh reads, never retries an old write.
Permission refusals remain403/access inventory, not fake sign-in success.

Tests use a hermetic service transport and fresh local fixture listener, not the
owner preview, live tasks, live DB or providers. Browser tests prove no protected
reads before sign-in, opaque cookie handoff, avatar keyboard behavior, sign-out,
and exactly one refused Delete across reauthentication. Main auth browser tests
exercise the extraction too. Inspect desktop and390px sign-in/menu captures.
