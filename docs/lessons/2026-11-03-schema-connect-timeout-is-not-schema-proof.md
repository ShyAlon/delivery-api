# Connection timeout is not missing schema

The schema gate creates one fresh app connection with a10s connection timeout and no application retry. A connection failure is not evidence for an automatic migration offer. Its diagnostic must report only safe endpoint class, port and configured timeout, then ask for project status/connection counts and local network/VPN checks. Do not expose a URL, project host, login or driver exception text. The launcher has already stopped its positively identified previous preview before reaching this gate, so do not blame that container without evidence.
