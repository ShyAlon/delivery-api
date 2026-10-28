# Login storage tests need no local auth

A browser test of editable email persistence need not create local accounts or call an external identity provider. Use the real signed-out PG application shell and an explicit failed-signin transport fixture. Keep assertions that email is editable and removable, password/token values never enter storage, and blocked storage does not prevent submission. Say that credential validation remains outside this frontend test.
