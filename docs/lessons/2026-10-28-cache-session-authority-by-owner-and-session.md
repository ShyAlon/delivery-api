# Cache session authority by owner and session

A liveness check runs under an owner's claims. Caching only session_id discards part of the authorization input. Bind cache entries to the verified owner/session pair, including negative entries. Prove the mismatch after warming the other owner's valid session, then prove neither legitimate identity was poisoned. Keep approved TTL, expiry boundary and database-error behavior unchanged. A hermetic signed mismatch reproduces the boundary flaw; it does not imply someone can forge a production-signed token.
