# Local models need explicit identity, route and bounded discovery

The owner chose Ollama but the local source could omit REQUI_PIN_PROVIDERS,
leaving the default paid provider selected. Its endpoint could point inside the
container, and an absent model ID could fall back to an unrelated engine catalog
model. Make these reviewed values explicit. Native Mac Ollama uses the Docker
host route, never container localhost. No paid/model fallback or behavior flag.

The Add model dialog also left model ID empty, inherited DeepSeek128k/16k bounds,
and called its local action Fetch prices. The endpoint never fetched vendor
prices: an empty model failed a downstream ceiling validator. Name the missing
ID/source/version/bounds before proposal, show a read-only installed-ID list and
label the zero-price review honestly. Leave local bounds blank rather than invent
a RAM-safe configuration.

Read-only checks list tags and already resident memory/context, never load/pull
or prompt. Compare actual owned container settings with host reachability.3GB
is the app cap, not native Ollama's RAM allocation. File size does not prove fit;
report unverified and obtain real installed model/config evidence before advising
exact bounds. Preserve accounting/spend guards and operator review.
