# A failed submit is not permission to discard a draft

Shared showError replaced the modal DOM. Create/rename/rerun and other modal forms using it lost their inputs. Keep the existing form nodes and show escaped text inline, with title-associated alert/focus for task naming. Do not save drafts or passwords to browser storage. Successful submit/navigation and explicit cancel retain existing behavior.

Auth, retry-phase and route-switch forms already show inline errors and retain values. Stale decision conflicts must remain non-submittable, but preserve the entered rationale read-only for copying rather than auto-discarding after700ms. This does not re-authorize a stale decision.
