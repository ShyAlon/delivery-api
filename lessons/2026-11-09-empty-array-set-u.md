# macOS still ships bash 3.2, and set -u turns empty arrays into landmines

A bare `req debug` died at argument ASSEMBLY - after every gate passed -
because bash 3.2 (still /bin/bash on every Mac) treats `"${args[@]}"` on an
empty array as an unbound variable under set -u. The feature worked; the
wrapper never let it start. The pre-merge tests ran the Python they exec,
never the shell that assembles the argv - and the sandbox's bash 5 cannot
reproduce the 3.2 error mode at all (the 4.4 behavior change is gated by no
compat option), so "it passed here" proved nothing about his machine.

Two rules. In any script that targets /bin/bash with set -u, EVERY array
expansion that can be empty is written ${args[@]+"${args[@]}"} - the guard
costs nothing when the array is full, so make it the file-wide invariant and
test the invariant, not the behavior. And a shell wrapper's test must exec
the wrapper: the thing between the user and the program is where the
program dies.
