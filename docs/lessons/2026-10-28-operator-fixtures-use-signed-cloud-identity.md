# Operator API fixtures use signed cloud identity

Do not copy local OAuth user/admin rows into PG just to keep a test fixture.
Operator capacity and metrics requests should pass the real verifier and live
session check with a signed regular-owner token and signed operator-email token.
Keep403/200 response contracts and add anonymous401. Name remaining metrics
SQLite storage separately;moving auth fixture storage does not retire metrics.
