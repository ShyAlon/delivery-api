# Gate fixture ports must preserve owner precedence

A waiting delivery gate and a live engine decision are different owners of user
input. Moving storage to PG must retain both precedence cases and final approval
recovery,not flatten them into a generic waiting state. Keep the explicit audited
open-gate policy regression too:it is deliberate provenance,not missing approval.
