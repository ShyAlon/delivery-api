# Restart replay tests need tenant-isolated enumeration

Reconciliation enumerates stored tasks. A fresh PG store must recover the same
owner's orphaned replay exactly once while another owner's running replay stays
untouched. Keep the independent reopen and all terminal/delete assertions,then
check cross-owner preservation through real RLS rather than a filtered mock.
