# Pinned acceptance producer wheels

Automatic delivery pull-request acceptance installs these real producer builds without private cross-repository credentials. The mandatory test still runs exactly two real HTTP/startup/PostgreSQL/child scenarios, with only vendor HTTP replaced. Missing/failed scenarios remain red. The manual item6 packaging workflow is unchanged.

- interview-engine source d6b3e690ec58caceb7446f0ae629d122f31cfa30
- spec-generator source adf43ff21ecd8252c1cffeff4909a5ff148a8c76

Built with `uv build --wheel` from clean pinned checkouts. Verify artifact hashes with `sha256sum --check vendor/acceptance-producers.sha256`. Rebuild and review both source revision and hashes whenever acceptance runtime sources change. These are test prerequisites, not a deployment or new runtime provider default.
