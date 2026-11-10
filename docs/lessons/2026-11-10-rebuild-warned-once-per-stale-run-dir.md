# 2026-11-10: rebuild printed about 30 identical warnings

## What happened
Every `req rebuild` printed one `[WARN] ignoring unreadable image-workflow run dir ...: accepted-image.json missing` per old run dir, twice (the receipt lookup runs twice).

## Cause
A run that never reached acceptance has no `accepted-image.json`. That is normal, but the lookup treated a missing file like a corrupt one and warned per directory.

## Fix
- Missing receipt: counted, one INFO line per process (count and where the old runs live). Never reused.
- Truly unreadable or corrupt files: still a WARN, once, with the count and the first path and error. Not silent.

## Rule
A warning is for something the owner can act on. Expected states are counted in one line; real faults stay loud. Never one line per item.
