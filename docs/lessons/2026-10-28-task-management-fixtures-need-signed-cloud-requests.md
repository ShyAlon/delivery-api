# Task management fixtures need signed cloud requests

A successful local admin collision/rename/delete test does not cover the cloud
request database. Use a real local signing key and live auth session through the
tenant middleware,then retain every response and source-artifact preservation
assertion. Direct cascade/provenance checks must still use the restricted tenant
store,not an admin connection used for fixture seeding.
