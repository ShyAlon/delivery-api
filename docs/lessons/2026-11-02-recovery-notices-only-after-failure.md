# Recovery warnings must describe an actual failure

Rerun support was unavailable for tenant sessions, but the page showed its
recovery warning for any started task. That made a healthy Start look broken.
Show this warning only for a failed task whose active phase failed; preserve
actual failed-interview explanation and supported generator Retry guidance.

Hardcoded nested sticky title offsets also stacked panel headings over the
changing task header. Remove that nested stickiness and keep the task header in
normal flow; only the global navigation remains sticky. Error notices and phase
details wrap instead of clipping. A desktop browser guard fails before the
healthy-banner fix and checks healthy/failed/retry states and header positions.
Actual pixels inspected after removing both overlapping sticky layers.
