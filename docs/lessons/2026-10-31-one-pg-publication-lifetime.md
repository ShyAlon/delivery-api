# One PG publication lifetime

C06: parent and isolated-child PG fences copied the exact running-context lifecycle. One owner now balances writer enter/exit and publication/Stop context restoration, including exceptions. Keep authority checks, connection acquisition, commit behavior and advisory-lock release host-specific. A shared lifecycle does not merge Stop, exit or billing facts.
