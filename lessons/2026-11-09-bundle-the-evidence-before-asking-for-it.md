# If the answer is "paste me three logs", the tool already failed

A midnight debugging round cost three manual pastes - a file path to cat, a
docker logs command, a state.json - each one a round trip between a tired
owner and a waiting diagnosis. Every paste is a failure mode: wrong run dir,
truncated output, an unredacted secret, a forgotten source. The old
`req debug` ZIP predated the run-dir evidence layout and carried only a
facts.json and one container log tail, so nobody reached for it when it
mattered: a bundle that does not contain the evidence you actually need is
not a bundle, it is another thing to explain.

Two rules. First, the debugging bundle must own the WHOLE evidence surface -
run-dir files (state.json, browser-smoke.log, whatever the gates wrote),
retained containers' logs and inspect facts, repo HEADs, host facts - and
name what it could not get, so "the ZIP" is the complete answer, never the
first of three messages. Second, keep the bundle's promise boring: read-only,
bounded tails, everything through the one scrubber, inspect fields whitelisted
so Docker Env can never leak, and the ZIP path as the last line printed. A
tool whose output you can paste without reading it first is a tool people
actually use at 1am.
