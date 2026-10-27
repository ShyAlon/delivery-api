# Missing diagnostic data is not an empty fact

Wrap optional Debug reads with explicit available/unavailable/policy-excluded
status, a fixed safe read-failed category and last-success time. Keep intentional
cloud omissions excluded without calling the endpoint. Task execution/classifier
fallbacks retain availability in Debug state; classifiers disabled by policy stay
excluded. A bounded cache retains times only, not diagnostic payloads. Never echo
server errors into the status. Failing-first section tests and browser inspection
cover the distinction between empty, failed and excluded.
