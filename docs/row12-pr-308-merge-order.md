# PR #308 merge order

Required order: #303 -> #304 -> #305 -> #308.

This supersedes the original PR body's independent wording. The branch includes
its predecessor changes to compose adjacent code/tests without conflicts. Merge
predecessors first so the remaining review is this PR's theme only. Browser tests
are in separate per-theme files. Local combined merge simulation passed; hosted
CI and full repository suite remain unverified. No automatic merge or deploy.
