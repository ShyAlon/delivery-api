# 2026-11-10: req rebuild cleanup - id forms never matched, and the docker reason was hidden

## Problem

`req rebuild --apply` stopped at step 7/7 after the typed "DELETE 8 CONTAINERS AND
16 IMAGES" with only "required command failed: docker; exit code 1; no state
advanced". No command, item or Docker message was shown, and the run had waited
~3 h at the prompt.

## Root cause

1. `image_workflow.command()` never echoes subprocess output, which is right for
   state reads (they can hold account config) but wrong for `docker rm/rmi`,
   whose output is a daemon message about the item.
2. The keep-list held full ids (`docker container inspect`: `sha256:<64 hex>`)
   or image names (`docker ps`), while the candidates came from `docker images`
   (12-hex short id). They never compared equal, so the running preview's image
   and the rollback container's image were listed for deletion. `rmi` of an image
   in use fails with a conflict, exit 1. The unit test used fake ids that all
   shared one form, so it could not see this.
3. Only preview-named containers were considered; an image used by any other
   surviving container was also listed.

## Fix

- Ids compare by normalized 12-hex key (`image_key`).
- The kept previous container's image is read by inspect of that container.
- Images of every surviving container (all of `docker ps -a -q`, minus the ones
  being removed) are never listed.
- Removals run through `docker_try`; each failure is collected as
  `docker <args> (<item>) exit N: <docker message>`, the remaining items still run,
  and the step fails naming every failure. Cleanup stays unmarked so a rerun re-plans.

## Rule

A fake in a test must use the exact id form the real tool prints. Failure text
for a command whose output is a plain daemon message names the command, the item
and the reason; state reads stay quiet.
