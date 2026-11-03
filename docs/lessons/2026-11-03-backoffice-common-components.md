# One shell is not enough

A single page shell still had divergent hand-built table cells, status rendering and confirmation composition. Audit cells missed mobile labels. Extract navigation into its own module; route every table through the same labeled cell path and keep confirmation focus in the existing shared dialog helper. Add the common footer once, not per tab. State colors use exact values, never regex matches that can turn "not completed" green.

Verify the worktree's source path explicitly when tests run beside an editable installation from another checkout. Otherwise a green UI test can be exercising the previous branch.
