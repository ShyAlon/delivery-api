# Answer runtime default

The installed paid runtime selects `answer-v1` when
`DELIVERY_ANSWER_ACCOUNTING_MODE` is absent. There is no enable step.
After merging, `req rebuild --apply` uses the existing migration and accepted
image gates. It does not run a paid model. The receipt migration must be present;
startup refuses the execution runtime when it is missing, before any dispatch.

Start and generator phases keep existing accounting. DeepSeek answers debit
0.1 credit and correct once from final usage. Ollama-only runtime keeps its
existing path. Invalid mode names fail closed.

## Two real answers

1. Owner merges and runs `req rebuild --apply`. Wait for its accepted-image and
   running-preview success. Use the same reviewed task/model and browser as the
   earlier measurement. No extra enable command.
2. On a task waiting for input, submit one normal answer. Wait for the next input,
   then submit a second normal answer. Do not submit twice or retry an error.
   These are real paid calls, started only by the owner under existing controls.
3. Run `req debug`. Compare the two answer traces: whole-answer duration, total
   parent/child syncs, trace completeness, receipt start/finish, and final published
   input. Each successful answer must have one settled receipt, the 0.1-credit
   estimate and its one correction. Missing trace or pending receipt means stop
   and investigate, not resend. Do not claim the local 29/26-sync result predicts
   live pooler or Docker latency.

## Explicit fallback only

If a future answer needs the old path, with tasks idle or waiting for input and
no pending/unreconciled answer, run:

```
req answer-mode --mode legacy --apply
```

The optional repair command saves only the mode through the shared private,
atomic settings writer and restarts the accepted preview. It does not apply
schema, build an image, submit an answer or run a provider. To restore the new
path, use `req answer-mode --mode answer-v1 --apply`. Without `--apply`, both are
dry review only. Restart failure leaves the saved selection intact and reports
that the running container might still have the previous mode. No automatic
rollback, answer replay or paid retry is performed.

## Local evidence and known limits

The full fast selection was run in two bounded chunks: 2,423 passed, one skipped,
five deselected, and one unchanged failure:
`test_customer_surface_boundary.py::test_all_customer_assets_have_no_private_operations_vocabulary`.
That failure is the inherited customer availability wording in
`web/route-availability.js`, reproduced on untouched main. It is not suppressed
or weakened here. The focused default/candidate/legacy, receipt failure and
full-task tests use owned Postgres and fake HTTP only, not live Docker/pooler
latency. Hosted Actions cannot start under the existing account billing block.
