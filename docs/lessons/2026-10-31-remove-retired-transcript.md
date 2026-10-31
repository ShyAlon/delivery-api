# Delete retired checkpoint transcript control flow

The cutover replaced checkpoint transcript recovery with an always-empty stub,
but callers still carried an alternative rendering branch and tests injected
invented checkpoint answers through that stub. That branch could not run in the
shipped implementation. Remove the stub, alternative renderer and its tests.

Recorded answer events and route switches remain the transcript authority.
Replacement tests require event order and prove length-only historical answers
are not turned into invented speech. Do not revive old checkpoint deserialization
or delete its old data during a code cleanup.
