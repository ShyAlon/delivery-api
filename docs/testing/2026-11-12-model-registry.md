# Pick a model, not a launch configuration

## Problem and first stage

The user selected a provider in the UI, while launch environment, database
model head, legacy engine default and producer output limits could name four
different choices. A local model also needed a paid-style operator approval.
A launcher wrapper would conceal this debt, not remove it.

The server registry now resolves exact provider:model identities. Approved paid
heads retain their money approval. Verified installed local packages get public,
versioned $0 capability records without an operator write. The selected record
is frozen using the existing task-generation model pin. Launch model/provider
flags do not override that explicit choice. Every producer stage's current output
budget is checked before Start; unsupported output budgets are refused, never
clamped. Existing tasks keep their retained pins.

The first supported local entries are qwen3.5:9b (preferred) and qwen3:8b. Model
package prefixes are checked against the public Ollama library records. A changed
package does not inherit capability by tag alone. The app endpoint must list the
installed package and report completion, explicit non-thinking control and a
large enough model context through /api/show. Missing old-server metadata means
not ready, never silent acceptance. No model pull, load or prompt occurs during
readiness. The UI still needs normal sign-in and the user still starts/reviews work.

## Capability sources checked 2026-11-12

- https://ollama.com/library/qwen3.5:9b : official packages 56671c2ab938
  (llamacpp Q4_K_M) and d63f830f28d5 (MLX), native context256K.
- https://huggingface.co/Qwen/Qwen3.5-9B : native262144 context;
  non-thinking example max_tokens32768, thinking examples81920. These are
  published examples, not a separately stated formal maximum output limit.
- https://ollama.com/library/qwen3:8b : official package500a1f067a9f.
- https://huggingface.co/Qwen/Qwen3-8B : native32768 context and output32768
  examples. No YaRN extension is assumed.
- https://docs.ollama.com/api/chat : native options num_ctx/num_predict,
  structured output format, non-thinking control and terminal usage.
- https://docs.ollama.com/api-reference/show-model-details : capability and
  context metadata; explicit thinking values identify supported controls.

Application envelopes: qwen3.5 uses131072 context, qwen3 uses32768; both use12288
maximum output. The qwen3.5 runtime envelope is deliberately below its262144
native limit. This is not a measured16GiB hardware-fit or quality claim.

The parent transport translates the existing accounted request to native
/api/chat, supplies the immutable pin's context and output bounds, requests
think:false and preserves response usage/length termination. It refuses unknown
request fields instead of silently dropping tools or sampling controls. SDK
legacy num_ctx cannot override the registry plan. A conservative wire-bytes plus
4096 framing estimate plus requested output must fit before journal/provider
dispatch. It is a protective estimate, not tokenizer measurement. Output cut at
the model cap remains an explicit producer failure, not a valid artifact.

## Proof and limits

Fail-first registry import/test captured in /tmp/registry-fail-first.txt.
Hermetic tests cover exact selection despite phi4/deepseek launch settings,
no operator pin, zero credit balance, authenticated HTTP, real disposable PG,
actual interview child and generator SRS child, native context, structured output,
stream terminal usage, immutable per-task pin, old-server refusal, paid/legacy
regression, pre-Start budget refusal and desktop/mobile picker.

Existing full-session cross-host fake-model acceptance passes through validation.
That check validates the shared orchestration and recovery path, not native
qwen3.5 model quality. Native interview and SRS transport proofs exercise actual
SDK serialization but fake model responses. No owner account, model or live DB was
used. No paid call, deployment or merge occurred.

The latest owner debug ZIP (06:27 on Nov12) contains answer timing/accounting but
no prompt/completion token counters or generator-phase prompt evidence. It cannot
establish whether32K would fit his largest real phase. A measured answer needs
provider_attempts.usage grouped by stage, and the serialized request/tokenizer
measurement when planning future requests. Do not turn artifact file size into a
prompt token count.

## Next stage

Model-aware splitting/input planning for smaller caps and large actual phases.
The current stage refuses incompatible output budgets before Start and oversized
actual requests before provider dispatch. It cannot predict future user-supplied
content growth at Start. Do not advertise universal full-cycle fit. Add further
model entries only with grounded identity, output examples/capability, current
server controls and fake end-to-end tests. No automatic model substitution.

## Final review additions

Public-origin deployments omit Ollama and do not probe it, even when an explicit
local endpoint is configured. Local unconfigured DeepSeek is not offered. Native
approved heads cannot bypass live package/capability checks. Read-only inventory
is cached for five seconds; Start explicitly refreshes it. Failed paid launch
verification also excludes that provider from selection.

Native transport sends `truncate:false` and `shift:false`, not Ollama defaults.
The supported server floor for these reviewed controls is 0.17.1, checked against
https://raw.githubusercontent.com/ollama/ollama/v0.17.1/api/types.go and
https://raw.githubusercontent.com/ollama/ollama/v0.17.1/server/routes.go .
Older or unverified server versions cannot offer these native registry entries.
A pre-dispatch plan refusal is retained as a fixed parent-generated message:
no call was made and no input was truncated. It is not an uncertain charge.

The vendor-wheel mandatory acceptance now requires four passing scenarios,
including both registry model families. Both native flags are asserted at fake
HTTP endpoints. Host memory, resource-fit estimation, and memory-pressure checks
are explicitly the next stage; 131072 is an application context, not a measured
fit on the owner's Mac. No model has been loaded on the owner's machine.

Pure Node label/progress fixtures now provide native history method stubs. The
shared browser instrumentation already used those methods on main; the fixtures
were missing the browser API, not an assertion failure to weaken.
