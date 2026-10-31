# One accepted-completion tail, no unused balance importer

C04: raw-provider, response-object and durable-staged adapters retain their evidence and replay rules but share the final ledger transition, meter resume and uncertain result check. Accepted settlement must not acquire fresh-work authority after Stop/hide. Frozen quote/rate, once-only debit and conflicting replay stay in the ledger.

Delete the uncalled historical account-scale importer and its two importer-only tests. Keep the historical migration table, browser-role denial test and existing stored evidence. This does not migrate accounts, change any current scale or delete data. The sampled CPU path is still wired and is not claimed retired; exact-child cutover remains separate.
