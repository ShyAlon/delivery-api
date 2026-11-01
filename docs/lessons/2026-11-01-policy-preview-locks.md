# Policy preview must not serialize with apply

Dry policy plan used both advisory and FOR UPDATE locks despite never writing. Keep locks only in the final apply transaction with bounded wait. Existing CLI already rolls back/closes preview before human prompts; do not blame an idle prompt without evidence. On timeout, use a fresh read-only connection to list current policy/advisory holders, PID/state/wait/ages only, not raw queries or credentials. An expired wait edge cannot be reconstructed with certainty. Never cancel or terminate another session automatically.
