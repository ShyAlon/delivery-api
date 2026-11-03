# Local execution must not depend on paid credentials

The default both-route launcher required a DeepSeek key before it could even show its launch confirmation. Runtime route isolation was not enough. A paid-pin refresh could also contact vendor sources even when no paid key existed.

Keep one launcher, but require a paid key only for explicit DeepSeek-only mode. Both-route mode can pass no paid key at all. Missing paid credentials stop paid refresh and dispatch, never a separately reviewed local route. Do not invent local models or bounds, bypass admission, change balances or waive tenant claims.

Prove the default launcher reaches confirmation with no paid setup and the actual startup/HTTP/child journey reaches a question with zero credits, a reviewed local pin and no paid pin/key. Keep the same ledger and exact zero-cost settlement checks. Missing local pins, mismatched bounds and nonzero local prices must still refuse.
