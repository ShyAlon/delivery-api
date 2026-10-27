# 2026-10-27 - A gate is only as real as its fixture, and a returned dict is a contract

Two drifts found while main showed 16 red tests nobody owned:

1. PR #270 added the shipped-JS parse gate to preview-exec (node --check on
   web/*.js) - and every preflight harness run died at source smoke, because
   the harness's fake repos tree never planted delivery-api's web directory,
   so the glob matched nothing. The gate was right; the FIXTURE no longer
   represented a real checkout. When a gate starts reading more of the world,
   every fake of that world must grow with it - plant the minimal faithful
   shape, never widen the gate to tolerate an empty tree.
2. 32242c2 made _runtime_version() return 'built_at': None for old images;
   the pinned contract omitted the key. A returned dict's exact key set IS a
   contract - add a key deliberately (here: omit when unset; the one consumer
   uses .get) and pin both shapes in the test.
