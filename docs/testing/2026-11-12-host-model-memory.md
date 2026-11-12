# Host-aware local model readiness

Stage 2 follows PR 758. No settings declare that a model fits. The existing
registry still discovers installed exact packages through read-only Ollama
requests and requires supported transport controls. Public-origin suppression
remains a defense against exposing a remote server's Ollama to customers.

## Host facts and estimates

The existing attended preview launcher, on macOS, reads `hw.memsize` and
`vm_stat` every ten seconds. It writes an atomic file in a dedicated directory,
mounted read-only into the app at `/run/requi-host`. It contains only host memory
counters, platform/architecture, method and timestamp, not credentials or host
identifiers. The app accepts it only for the known native-Mac route
`http://host.docker.internal:11434`, not arbitrary configured endpoints. It must
be less than 30 seconds old. The launcher removes it on exit. Unsupported hosts,
failed sampling or missing/stale readings yield "Host memory not checked".

Total physical memory is a host measurement. Available memory is a reclaimable
page estimate: free + inactive + speculative, multiplied by the reported page
size. Purgeable pages are not added a second time. This is not a memory
reservation, a pressure forecast, or Apple's definitive available-memory metric.
No container memory or swap capacity is substituted for native host capacity.

The working-set estimate is installed package bytes as a weight proxy plus:

- Qwen3-8B: 36 full-attention layers, eight KV heads, head dimension 128.
- Qwen3.5-9B: eight full-attention layers, four KV heads, head dimension 256;
  24 recurrent layers have a separate state estimate.
- fp16 key/value storage and one parallel sequence are assumed.
- 1 GiB application-selected runtime/allocator reserve, not a vendor guarantee.

The KV formula is `2 * full_attention_layers * kv_heads * head_dimension *
2 bytes * context_tokens`. Qwen3 at 32768 yields 4.5 GiB; Qwen3.5 at 131072
has a 4 GiB attention cache term. The hybrid model is not treated as 32 full
attention layers. A changed runner, cache quantization or server parallelism can
change the actual requirement. The estimate is always labeled, never a claim of
measured fit or performance. No context is shortened.

Already-resident exact-model bytes are credited only at the exact selected
context. Unified GPU memory is not added to system capacity or counted a second
time alongside resident size. If estimated incremental demand exceeds current
estimated available headroom, the UI disables Start and explains the numbers.
The model record remains present for an honest reason, not an invisible fallback.
Start refreshes discovery and reads fresh telemetry again. Missing telemetry
allows the existing explicit Start but never claims memory readiness.

## Actual load verification

After the first native response on each parent transport, a bounded read-only
`/api/ps` request verifies the exact loaded context and positive resident/VRAM
allocation. The observation goes into the existing diagnostics. No second
inference, load, pull, unload or automatic retry is issued. A missing observation
or context mismatch stops continuation with a fixed parent-generated error.
The inference may already have completed; this is not described as a no-call
failure. Future registry reads can report the currently loaded exact allocation.
A positive pre-load estimate does not guarantee this verification will pass.

## Sources inspected

- https://docs.ollama.com/api/ps : loaded size, VRAM and active context, not host capacity.
- https://docs.ollama.com/api/tags : installed package bytes are disk footprint.
- https://docs.ollama.com/faq : parallel inference and cache settings affect memory.
- https://huggingface.co/Qwen/Qwen3-8B/raw/main/config.json : full-attention shape.
- https://huggingface.co/Qwen/Qwen3.5-9B/raw/main/config.json : hybrid attention and recurrent shape.
- https://developer.apple.com/library/archive/documentation/Performance/Conceptual/ManagingMemory/Articles/VMPages.html : `vm_stat` page counters.
- https://keith.github.io/xcode-man-pages/vm_stat.1.html : free/inactive/speculative/purgeable definitions.
- https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md : pressure and reclaimability differ; no single page sum guarantees a fit.

## Validation boundary

Tests use fake host counters and fake provider HTTP, real browser controls and
disposable PG. No owner model, private account, production DB, paid call or
platform deployment is used. Native first-interview and SRS-child tests inspect
the loaded-plan observation. Both attended launch paths own sampler cleanup.
Desktop/mobile screenshots inspect the picker and estimate note. Host command
behavior on a real Mac and model quality/performance remain unmeasured here.
