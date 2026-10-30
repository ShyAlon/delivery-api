# Match enum identity, not optional documentation brackets

The preview's public-only pin step failed on `API model identities disagree`.
Fresh pricing and API documentation still agreed on deepseek-flash and
deepseek-v4-pro. The API renderer stopped surrounding enum values with square
brackets, but the parser required them. Old/current captured HTML produces an
identical full parsed result after the fix, including model version, both rate
periods, limits and peak calendar rule.

The shared parser now accepts both complete enum presentations within exactly
one model field. Exact membership and cardinality remain mandatory; unmatched
brackets, missing/extra/duplicate identities, renamed models and duplicate model
fields still fail closed. Neither model names nor numerical pins were changed.
The preview and background vendor check share this cause and this fix.

Fail-first: captured current API HTML failed under the original parser. A
hermetic regression compares the complete old/current parsed output. Targeted
parser/vendor/provider/pin/preflight gate passed 69 checks, then the expanded
parser and pin gate passed 12. The actual pin command also succeeded with fresh
public GETs and owned disposable PostgreSQL. No provider calls, real database,
preview/container launch, deployment or spending. Docker and hosted CI were not
run. The user's protective 128000-context / 16384-output limits and retail
formula remain unchanged; the published higher vendor maximum is not adopted.
