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
        # Wake-and-join owns shutdown; merely setting the flag leaves the
        # sampler alive after monitor cancellation (and races the next test).
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)

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
            frames = list(reversed(traceback.extract_stack(frame)))
            fmt = lambda f: '%s:%d %s' % (f.filename.rsplit('/', 1)[-1], f.lineno, f.name)
            # The innermost frames say what blocked; the log keeps 500 characters per field, so the delivery frames that
            # asked get their own part instead of being cut off after the library frames (2026-11-10: the one remaining
            # 437 ms stall showed only psycopg and contextlib frames).
            ours = [f for f in frames[4:] if '/delivery_api/' in f.filename or '/interview_engine/' in f.filename][:6]
            self._emit(int(late * 1000), ' < '.join(fmt(f) for f in frames[:4]) + ' || asked by: ' + ' < '.join(fmt(f) for f in ours))
