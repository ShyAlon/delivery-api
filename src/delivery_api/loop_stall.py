"""Names WHAT blocked the event loop (2026-11-10).

loop.lag only says the loop was late (974-2652 ms around answers and stream connects). The loop cannot
report its own stack while blocked, so a watchdog thread samples the loop thread's stack while the loop
misses its beat. Read-only: it never touches the loop, a request or the database."""
import sys
import threading
import time
import traceback

STALL_MS = 400


class StallWatchdog:
    def __init__(self, emit, loop_thread_id, stall_ms=STALL_MS, poll_s=0.1):
        self._emit = emit
        self._tid = loop_thread_id
        self._stall = stall_ms / 1000.0
        self._poll = poll_s
        self._beat = time.monotonic()
        self._reported = False
        self._last = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='loop-stall-watchdog', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def beat(self):
        self._beat = time.monotonic()
        self._reported = False

    def _run(self):
        while not self._stop.wait(self._poll):
            late = time.monotonic() - self._beat
            if late < self._stall or self._reported:
                continue
            self._reported = True   # one stack per stall; the stall's total is loop.lag's job
            if time.monotonic() - self._last < 5:
                continue
            self._last = time.monotonic()
            frame = sys._current_frames().get(self._tid)
            if frame is None:
                continue
            frames = traceback.extract_stack(frame)[-8:]
            self._emit(int(late * 1000), ' < '.join('%s:%d %s' % (f.filename.rsplit('/', 1)[-1], f.lineno, f.name) for f in reversed(frames)))
