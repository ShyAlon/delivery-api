# 2026-11-10: the event loop stalled 1-2.6 s and nothing said on what

Evidence (task 7f3b86686274): loop.lag 2268, 1628, 2652 and 974 ms around answer completion and stream
connects. The stream.disconnect field loop_blocked_ms (11-15 s) is SUMMED THREAD time of 17-23 stream reads
(about 0.7 s each, max 1.09 s), not loop blocking (stream_events_timed says so); stream reads already run in the
threadpool. So the stream is not shown to block the loop. What blocks it is unknown.

Change: a watchdog thread samples the loop thread's stack when the loop misses its beat by 400 ms and logs
loop.stall.stack with the 8 innermost frames, one per stall, at most one per 5 s. The lag monitor ticks at 0.2 s.
Read-only. The next debug ZIP names the blocking function; fix that, not a guess.

Rule: do not move work off the loop on a metric whose name promises more than it measures.
