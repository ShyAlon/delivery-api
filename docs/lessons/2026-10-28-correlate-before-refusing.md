# Correlate before refusing

CSRF/control-plane/operator refusal can return before endpoint middleware assigns
a reference. Put diagnostic context around the whole request, including refusal
branches, and reset only after completed activity records. Otherwise early refusals
have no reference and ordinary completion warnings lose their task scope.

Keep status codes, auth checks, cache headers and mutation fences unchanged. A
reference is evidence, not authority or a bypass. Do not log credentials or body.
