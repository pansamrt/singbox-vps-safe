# Smart: IPv4 + IPv6 dual-stack support

**Stage:** manually reviewed test candidate; **no VPS has been accessed**.

## Listener modes

| Install selection | sing-box listen | Expected family |
| --- | --- | --- |
| dual (default) | `::` | IPv4 + IPv6 mapped clients, subject to kernel support |
| ipv4 | `0.0.0.0` | IPv4 only |
| ipv6 | exact IPv6 literal assigned to VPS | IPv6 only; wildcard `::` rejected |

The dual listener is **one inbound** on the chosen port, rather than two
same-port inbounds that might collide. Both TCP and UDP use identical mode
selection. The program's network preflight tests an IPv4 wildcard bind,
then an IPv6 wildcard bind with `IPV6_V6ONLY=0` and refuses a conflict on
**either** family before installation mutates the system. The check is
best-effort (ports could be taken after preflight). Native sing-box still
must successfully start and accept connections.

An IPv6-only listen must be a **concrete address**, such as the one shown
by `ip -6 addr show scope global`. A public IPv6 is not automatically
allocated; a documentation-only IPv6 address will fail installation.
Use `--network-mode ipv4` when IPv6 support is not available.

```bash
# Always inspect first; no changes
bash install.sh --plan

# Default dual stack
sudo bash install.sh --install-reality 8443 www.example.org

# Explicit IPv4-only
sudo bash install.sh --install-reality 8443 www.example.org --network-mode ipv4

# Explicit IPv6-only: address must exist on this VPS
sudo bash install.sh --install-reality 8443 www.example.org \
  --network-mode ipv6 --listen YOUR_ASSIGNED_IPV6
```

No change to DNS, cloud-provider firewall, SSH, router, or existing services.

## Hysteria2 / TUIC / AnyTLS generator

The generator's default is also dual-stack. Add `--network-mode ipv4` or
`--network-mode ipv6 --listen YOUR_ASSIGNED_IPV6` to its usual arguments
when a single family is required. Legacy explicitly provided `--listen`
values remain usable, but ambiguous IPv4-mapped IPv6 literals are rejected.

## Subscription export

```bash
# A DNS hostname with working A and AAAA records
sudo bash install.sh --export-clients vpn.example.org

# Alternatively generate TWO client nodes for each configured protocol:
sudo bash install.sh --export-clients \
  --server-ipv4 YOUR_PUBLIC_IPV4 --server-ipv6 YOUR_PUBLIC_IPV6
```

Numeric IPv6 server addresses appear in JSON/YAML as ordinary unbracketed
IPv6 values. URI-style links wrap IPv6 addresses in brackets, e.g.
`hysteria2://PASSWORD@[2001:db8::25]:8443?sni=vpn.example.org`.
The two exported client nodes reuse the *same* server credentials. There
is no second listening server on that port.

## Private local subscription service

`--subscription-init` now defaults to BOTH 127.0.0.1 and ::1, but **only**
on loopback. For an IPv6-only test host:

```bash
sudo bash install.sh --subscription-init --loopback-mode ipv6
sudo bash install.sh --subscription-serve
```

The printed URL begins with `http://[::1]:8787/TOKEN/auto` on IPv6-only.
It cannot be used directly by clients outside the VPS. Remote subscription
clients need a separately configured, correctly validated HTTPS reverse
proxy to `[::1]:8787` (or 127.0.0.1 for the default setup), no access
logging of bearer tokens, and DNS/certificate configuration under owner
control. No third-party subscription conversion or QR API is used.

## Verified isolated Linux integration

- Real native sing-box **v1.14.4**, downloaded from its official release
  with fixed SHA256, tested on Debian 12 and 13.
- IPv4 TCP and IPv6 TCP SOCKS inbound, native byte forwarding via loopback.
- IPv4 and IPv6 **Hysteria2 QUIC/UDP** data transfer with the actual sing-box
  client and server; a temporary test CA verifies TLS (no insecure bypass).
- IPv4-only and concrete IPv6-only TCP listeners reject the opposite family.
- TCP/UDP port collision tests for both IP families.
- Local subscription service serves IPv4/IPv6 loopback with independent
  token checks; IPv6-only subscriber refuses IPv4.
- Installer with simulated systemd refuses an unassigned IPv6 address before
  any installation mutation; default Reality config is inspected.
- Dual-public-IP subscriptions are exported with correct IPv6 URI brackets.

Automated workflow:
[Debian runtime integration](../.github/workflows/debian-runtime.yml)
and [owner-install integration](../.github/workflows/manual-install-ci.yml).

## Outside current acceptance scope

- No real public Internet IPv6 routing, cloud firewall/port rules, assigned
  VPS IPv6, or end-to-end public client-to-VPS measurements were tested.
- Real Debian full-VM systemd reboot/restore and all protocol implementations
  have separate pending gates (see README).
- No Ubuntu / ARM64 dual-stack hardware tests have been completed.
- No Linux sysctl, firewall, ip6tables, DNS or interface state is modified
  by these features. A damaged/missing IPv6 stack causes preflight to fail
  instead of silently downgrading the requested dual-stack mode.
