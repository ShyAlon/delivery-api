# Static module imports need matching preload entries

Three current static dependencies were absent from the modulepreload list:
debug-package.js, result-freshness.js, debug-sections.js. The unchanged import
closure assertion failed with 1 failed / 8 passed in 0.51s.

Add only these three preload entries. No module behavior, test assertion,
selection or coverage changes. Existing debug_path module: 9 passed in 0.47s.
Held PG-first debug_path: 9 passed in 0.43s. Real signed PG browser gate
shell smoke also passed; visual inspection is recorded in the PR report.
No deployment or bootstrap flip. Hosted CI/full suite unverified.
