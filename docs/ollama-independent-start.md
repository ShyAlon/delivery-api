# Local Ollama without DeepSeek setup

After merging, use the normal `req preview --apply` launcher. No DeepSeek key, paid pin, paid expense policy or prepaid credits are needed to start Ollama. The same launcher still offers both routes. DeepSeek stays unavailable until its own prerequisites are configured; it never substitutes for Ollama.

Still required: the normal PostgreSQL/Supabase preview settings and schema, a reviewed default Ollama pin with exact zero prices and a context/output bound, the configured Ollama server/model, and the signed-in owner's admission row. Review the local pin through Back office > Model pins. The launcher does not create or approve it. A missing or incorrect local pin refuses Start.

Explicit DeepSeek-only launch still requires its key. Existing configured paid keys remain private and paid dispatch keeps all existing approval, pin, balance and expense-policy gates. No migration or new paid approval is needed for this code change. No live setup or model run was performed during implementation.
