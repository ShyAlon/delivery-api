# Unreadable is not empty

A missing file and an unreadable file both have no rows, but they mean different
things. Preserve the missing-file compatibility contract and expose safe error
class for unreadable evidence. A package manifest should record missing/unreadable
source outcomes as unavailable, while keeping any partial data and bounds visible.
Never send a filesystem exception's raw message or pretend missing evidence is
an empty successful collection.
