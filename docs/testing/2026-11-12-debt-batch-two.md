# Debt batch two: legacy payment and Stop-query ownership

Base is merged delivery-api757 main eeadd8f1. No live database, schema, paid
execution or deployment is involved. This branch is not ready to merge.

## Proven first unit: Stop receipt fanout

confirm_child_session_quiescent read one retained completion receipt per job.
The five-job real PG test failed with five reads. A single left join now returns
all jobs with their exact child-completion-v1 receipts. Python still requires
state done and boolean reaped exactly True for every job; missing, false, string
and numeric proofs refuse. No jobs still refuses. The existing session Stop,
provider-attempt and retail-segment checks are unchanged. No lease-expiry,
completed-task or empty-registry shortcut is introduced.

106 focused tests pass, including Stop/auth revocation during a real held child
call, real zero-cost installed child, retained receipts, stopped-generation RLS,
held accounting, owner/operator Stop and missing child-proof behavior. Fifteen
focused tests also passed before the stricter receipt cases were added. These
are overlapping groups, not counts to add together.

## Legacy payment inventory in progress

answer-v1 is the default for DeepSeek interview answers. It is not a reason to
delete all WorkerAdmissionGateway/provider_attempts code: interview Start,
local Ollama and generator phases still use the installed child broker's per-call
path. ChildGeneratorEngine and SameHostProducerWorker still depend on those
boundaries and durable recovery evidence. Explicit legacy answer fallback also
still exists. Preserve those live callers unless independently replaced under
the approved scope, not silently reclassify them retired.

Candidate genuinely disconnected path: send_job_gateway in admission_handoff.py
has no application caller, only its own tests. current_worker_gateway is called
only by that handoff, and worker_admission_channel only by that handoff. The
runner explicitly refuses non-isolated subprocess dispatch. Continue checking
producer-side descriptor receivers, external entry points and tests before
removing this closed route. Do not delete the shared gateway/ledger merely
because this transport is retired.

## Remaining

Finish caller and recovery inventory, prove the installed parent-broker path
before deleting disconnected protocol/helpers/tests, then wider mixed paid/local/
Stop/accounting regression, final fast gate and fresh-main mergeability. No PR
created for batch2 yet. Diagnostic cleanup is separate interview-engine100.
