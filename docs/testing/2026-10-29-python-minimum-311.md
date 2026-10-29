# Declare Python 3.11 minimum

Owner decision:raise the minimum to3.11 rather than add a Python3.10 digest compatibility helper. Update pyproject and regenerated uv lock,stale local UI/build installation messages,and README runtime/venv guidance. No classifiers or3.10CI matrix entries exist. Docker,CI,ops scripts and full local test runner already use/require3.12;leave those stronger toolchain requirements unchanged. Historical lessons retain their original3.10 evidence. No digest implementation or assertion weakening.

Failing-test-first metadata regression:red1failed0.09s before update;green1passed0.05s on existing3.10 interpreter. The test checks project/lock agreement and exclusion of3.10/inclusion of3.11+3.12. Existing unsupported3.10venv was not upgraded or replaced.

Installed isolated CPython3.11.17 and a separate temporary environment for verification. uv lock --python3.11 --offline regenerated successfully;uv lock --check --python3.11 --offline resolves48packages. Explicit lock check using3.10 correctly refuses the interpreter. Declaration/local UI/build script slice:32passed1.05s on3.11. Bash syntax checks pass for run-ui,build-dist,test-local.

All16retained real-producer revision-stage cases were executed on3.11 with producer source/dependencies:4passed12failed in51.68s. The hashlib.file_digest error is gone. Eleven failures stop at missing interview_engine source;one stops at missing Playwright managed Chromium in the fresh environment. These are incomplete local integration prerequisites,not proof of passing behavior. No bypass,skip or assertion change was added. Full16green,full suite,Python3.12run and hosted CI remain unverified.

Lesson:the package's declared floor must match standard-library APIs it uses. Raising that floor also requires honest venv guidance and a real minimum-version attempt,not merely changing metadata or treating the disappearance of the first exception as a full integration pass.
