# Fixture ports must not implement refused rerun policy

PG deliberately rejects legacy prepare_rerun because a generation reset would
leave immutable session binding stale. A fixture migration must not bypass that
refusal,patch generation rows or weaken freshness assertions. Move independent
freshness cases to PG and preserve the original local rerun regression unchanged
in a clearly named legacy module until its policy is decided separately.
