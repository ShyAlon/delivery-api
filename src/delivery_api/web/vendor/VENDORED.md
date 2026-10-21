# Vendored browser libraries (no build step)

| file | package | version | license | sha256 |
| --- | --- | --- | --- | --- |
| preact.module.js | preact (dist/preact.module.js) | 10.29.8 | MIT (preact-LICENSE.txt) | c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8 |
| hooks.module.js | preact/hooks (hooks/dist/hooks.module.js) | 10.29.8 | MIT (preact-LICENSE.txt) | a6ee626f2d01570592dd569a792e3f050154aa02890eead8c223fa3ed5aa3d5a |
| htm.module.js | htm (dist/htm.module.js) | 3.1.1 | Apache-2.0 (htm-LICENSE.txt) | ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4 |

Sources (fetched 2026-10-21):
- https://registry.npmjs.org/preact/-/preact-10.29.8.tgz (registry integrity sha512-... recorded in npm; 10.29.8 published 2026-08-01, last 10.x)
- https://registry.npmjs.org/htm/-/htm-3.1.1.tgz (integrity sha512-983Vyg8NwUE7JkZ6NmOqpCZ+sh1bKv2iYTlUkzlWmA5JD2acKoxd4KVxbMmxX/85mtfdnDmTFoNKcg5DGAvxNQ==)

Why vendored: the image is Python-only (no Node). These three ES modules are served
from /assets like the rest of the UI; tests recompute the sha256 values above.
