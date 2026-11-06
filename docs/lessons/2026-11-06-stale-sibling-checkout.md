# 2026-11-06: A clean checkout is not a current checkout

Problem: `req image --accept-local` fast-forwards only delivery-api, and preflight required each sibling checkout merely to be clean. A sibling days behind origin/main passed silently and was baked into the image, so the tested image could differ from the current sources with no signal.

Lesson: "clean" answers the wrong question. Before building from local sources, fetch and require each checkout to contain origin/main (HEAD ancestor check), and refuse loudly naming the repo, both SHAs, and the exact pull command. Every manual step is a failure mode; the refusal message is the repair path.
