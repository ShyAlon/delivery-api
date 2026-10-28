# Browser fixtures must drain their streams (2026-10-28)

The signed PG audit browser case passed every UI check but failed teardown twice:
the server thread was still alive after ten seconds. Browser close alone cannot
replace application shutdown. With lifespan disabled, active progress streams
never received the application's stop event and uvicorn waited for them.

The fixture now captures its server loop and schedules stream-stop notification
there before graceful shutdown. It still asserts the server thread stops.
Production stream behavior is unchanged. A separate transient tooltip visibility
red showed an immediate visibility read during CSS transition;the case now waits
for visibility before retaining its original assertion.

Fail-first fixture signature guard rejected the old browser dependency. Combined
artifact/audit,cloud API and bootstrap checks passed44,skipped0 in15.85s and again
15.08s. Keep presentation payload mocks explicit:these are browser contracts,
not proof of real artifact byte downloads. Hosted CI and full-suite unverified.
