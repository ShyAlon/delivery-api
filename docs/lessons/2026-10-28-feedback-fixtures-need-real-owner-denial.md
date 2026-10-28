# Feedback fixtures need real owner denial

A local CRUD success does not prove private feedback stays private. Keep the
entire existing upsert/target/audit flow on signed tenant PG,and try the same
list/update/delete with another live owner before the owner's continuation.
The foreign failures must not change the owner's content or block its later edit.
