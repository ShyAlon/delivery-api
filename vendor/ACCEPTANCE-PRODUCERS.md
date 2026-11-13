# Pinned acceptance producer wheels

Automatic delivery pull-request acceptance installs these real producer builds without private cross-repository credentials. The mandatory test still runs exactly two real HTTP/startup/PostgreSQL/child scenarios, with only vendor HTTP replaced. Missing/failed scenarios remain red. The manual item6 packaging workflow is unchanged.

- interview-engine source 39b6efd4467e01a796831e90d80b6164e0a5e93e
- spec-generator source 6c5fa0e497651bcf552fc83e9e3a9eb93c454bfe

Built with `uv build --wheel` from clean pinned checkouts. Verify artifact hashes with `sha256sum --check vendor/acceptance-producers.sha256`. Rebuild and review both source revision and hashes whenever acceptance runtime sources change. These are test prerequisites, not a deployment or new runtime provider default.
