# A free local route must not require purchased credits

The production Ollama gateway intentionally uses shared admission and strict usage accounting. Its require_prepaid=True path also demanded positive balance before dispatch, even for zero expense/retail prices. A missing or zero account reproduced BrokerPaused with no HTTP and no committed attempt in migrated disposable PG.

Keep shared gateway and ledger checks. Exempt only Ollama quotes whose four fixed-point rates are exact integer zero, with no paid cache/formula/vendor/estimate components, from prepaid authorization. Zero usage or a free-looking DeepSeek quote is not exempt. Settle local usage through the provider ledger, without creating credit charge records. No credits added, policy changes, migration or live data action.
