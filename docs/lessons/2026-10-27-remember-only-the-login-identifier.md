# 2026-10-27: Remember only the login identifier

The sign-in form calls the email field username. Remember that editable email,
not a display name, password, authentication reply, or token. Set input.value
rather than interpolating saved browser data into HTML.

Persist a valid submitted sign-in email, including a failed sign-in so a typo
in the password does not erase the convenience. A replacement submission wins;
clearing the input removes the saved value. Catch localStorage failures so
private-mode/storage restrictions cannot block sign-in. Native Enter submission
remains untouched. The preference is browser-origin-local, not account sync.

Failing-first browser coverage proves initial blank, prefill after reload,
replacement, clearing, password absence, Enter submission and blocked-storage
behavior. Desktop and mobile screenshots were inspected.
