# Stale-answer fixture ports must preserve current truth

A rejected input must report the current task/phase and record its error without
adding an answer transcript. Preserve the same durable snapshot when restoring
external-input ownership. A storage port must keep those exact facts and approval
audit provenance,not merely assert a generic conflict status.
