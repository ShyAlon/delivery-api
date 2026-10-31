# Stop is not Delete

The owner asked for Stop and Delete to be possible, not for every Stop to delete the task. A combined-only action changed the user's intent.

Worker Jobs now has separate View, Stop and Delete buttons. Stop confirms the task name and billing consequence, persists the same durable Stop, and keeps the task and saved work visible. Delete separately confirms Stop plus removal from the normal view. Both use the same maintenance boundary, and neither invents worker exit or accounting settlement.

Tests retain the previous Stop+hide and accounting checks, add Stop-only visibility plus later Delete, and update browser button counts and confirmation choice. The combined-confirm browser test now selects Delete, not Stop. No protection assertions are weakened.
