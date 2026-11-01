# Price refresh failure: warn below24h, error at24h

The pin launcher previously refused any fetch failure. Runtime did the opposite: even a three-day-old snapshot could continue after refresh failure. Current owner policy reconciles them: refresh at launch; use stored prices only strictly below24h after the last successful fetch/verification; at24h or with no valid stored price, stop new work. Fetch failure never renews the timestamp.

pin.py stores public price settings plus fetched_at under runs/vendor-price-cache.json (ignored); override path with REQUI_PRICE_PIN_CACHE. Atomic replacement prevents truncated cache writes. Offline fallback validates exact model/bounds, source snapshot and timestamp. Successful launch fetches replace the cache; parse/validation failures still refuse rather than silently caching them. This is not the proposed general review-and-pin model flow; that remains a separate choice. No schema changes or live actions.

Runtime keeps its last successful verification during outages, refuses failed refreshes at24h, and cannot reuse a cached warning across the24h boundary. Existing changed-term warning semantics remain, except a failed confirmation at24h now refuses. Frozen accounting evidence for already-admitted attempts is not rewritten.
