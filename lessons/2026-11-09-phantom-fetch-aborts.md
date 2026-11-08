# A "failed request" is an event, not a fact - check whether a response arrived

The preview browser smoke counted every Playwright `requestfailed` as fatal,
and headless Chromium misreports COMPLETED `fetch()` calls as
`net::ERR_ABORTED`: the response event fires, the page's own fetch resolves
204, and the failed event fires anyway. XMLHttpRequest and document loads
report correctly; fetch does not. So the page's fire-and-forget client-log
POST landed its 204 on every healthy boot and still printed as a fatal
"request failed" line - exactly the two lines that killed Shy's rebuild at
the smoke gate while telemetry_success was True.

The misreport was first misread as a close-time race (the browser closing
with the POST in flight), and the fix aimed at the race: drain in-flight
telemetry before close. The drain could never work - the phantom abort fires
when the RESPONSE arrives, even for instant requests, and `requestfinished`
never fires for these fetches at all. Only a tighter reproduction (fetch vs
XHR, instant vs delayed, kept open vs closed) separated the artifact from
the timing. A fix aimed at a theory of the failure, however reasonable,
verifies nothing; aim the repro at the event itself.

Rule: an event stream is evidence, not verdict. When two events contradict
(response 204 vs request failed), decide which is the source of truth for
the gate's question - here the response, because the gate asks "did the
network exchange succeed" - and classify the contradiction by name. A
failure report whose request already produced a captured response is a
misreport, tolerated by name; a failure report with no response is still
fatal.
