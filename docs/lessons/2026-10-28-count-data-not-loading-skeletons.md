# Count data, not loading skeletons, 2026-10-28

Dashboard loading cards also use .task. Waiting for the first .task can capture
three skeletons as the cancellation baseline, then compare them against one real
task. The combined source/browser regression exposed this. Wait for the actual
task heading before taking the count. Keep the before/after assertion unchanged.
