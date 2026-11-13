# Pinned acceptance producer wheels

Automatic delivery pull-request acceptance installs these real producer builds without private cross-repository credentials. The mandatory test still runs exactly two real HTTP/startup/PostgreSQL/child scenarios, with only vendor HTTP replaced. Missing/failed scenarios remain red. The manual item6 packaging workflow is unchanged.

- interview-engine source 5b8796ad33796f6dc84a6c522d965ba5263f38a5
- spec-generator source eca71eb894746990ef8754ad635d482ee670b8e0

Built with `uv build --wheel` from clean pinned checkouts. Verify artifact hashes with `sha256sum --check vendor/acceptance-producers.sha256`. Rebuild and review both source revision and hashes whenever acceptance runtime sources change. These are test prerequisites, not a deployment or new runtime provider default.
