# Pinned acceptance producer wheels

Automatic delivery pull-request acceptance installs these real producer builds without private cross-repository credentials. The mandatory test still runs exactly two real HTTP/startup/PostgreSQL/child scenarios, with only vendor HTTP replaced. Missing/failed scenarios remain red. The manual item6 packaging workflow is unchanged.

- interview-engine source fdc99f5a2f3a845f0fc452a48dec90c6dfdeea8e
- spec-generator source d31345626771245867d1b08f7969329d85718b4c

Built with `uv build --wheel` from clean pinned checkouts. Verify artifact hashes with `sha256sum --check vendor/acceptance-producers.sha256`. Rebuild and review both source revision and hashes whenever acceptance runtime sources change. These are test prerequisites, not a deployment or new runtime provider default.
