# Connection displays must use the same parser as the connection

A successful connection did not validate urllib's display of its host/user.
Parse PostgreSQL URIs and key/value conninfo with libpq; after connecting use the
connection's effective host and user. Only then derive the project candidate from
a direct host or pooler username. Shared pooler host alone remains unknown. API
URLs retain ordinary HTTPS parsing. Never print the input, password or parser
error. Tests use fake percent-encoded/special credentials and observed malformed-
display shape; no user database accessed. Regression failed first.
