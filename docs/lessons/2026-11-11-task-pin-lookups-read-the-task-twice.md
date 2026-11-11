# 2026-11-11: resolving a retained task pin used two transactions

## Problem
DurableGateways.for_task read a full task projection to get its generation, then opened another transaction to read
that generation's immutable model-pin review. Each lookup cost six network syncs for two small facts.

## Change
Read the visible task's current generation and matching model-pin review with one left join and one transaction.
A missing task still raises KeyError. A missing current-generation pin still follows the existing reviewed capture path.
Existing pins still come from PostgreSQL every time; no authority or model-pin cache was added.

## Measured
An already-retained generation pin lookup costs three syncs instead of six: identity, one joined read, commit.
It also avoids the unrelated task event/artifact projection. It does not remove the separate public-head reads made by
models/transport readiness; those are a different path and are not claimed as saved here.

## Tests
Fail-first transaction/query/sync budget; current generation selects its own pin; a missing newer-generation pin does
not fall back to an earlier generation; foreign-owner and deleted tasks do not resolve. Existing model-pin freeze and
restore tests cover first capture and changed public defaults.

## Rule
Join facts that must describe the same generation. Do not replace a durable read with an in-process cache just to save trips.
