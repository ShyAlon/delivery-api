# Native manifest SQL keeps byte authority

A query port must preserve the publication transaction, not merely make new SQL pass. Inject failure after bytes/members are written but before readiness, then check the prior snapshot is identical and no partial member remains. Check superseded bytes remain hash-addressable while manifest-name reads stop exposing them. Keep foreign-owner byte and producer reads refused. A common task projection exemption is not proof that all transitive SQL is native.
