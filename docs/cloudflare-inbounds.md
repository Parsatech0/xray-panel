# Cloudflare proxy (🟠 orange) vs DNS-only (⚪ grey) — what to use per inbound

Short version: **Cloudflare's proxy only understands HTTP(S).** Any inbound
that speaks plain HTTP-based traffic (WebSocket, gRPC over HTTP/2) runs fine
behind the orange cloud; anything that needs raw TCP passthrough or Xray's
own TLS hijinks must bypass it.

| Inbound as configured in the panel              | 🟠 Orange (proxied) | ⚪ Grey (DNS only) | Why |
|-------------------------------------------------|:---:|:---:|---|
| VLESS + **WS** + TLS                             | ✅ best choice | ✅ | CF forwards HTTP/WebSocket traffic; the client connects with your domain |
| VLESS + WS **without TLS**                       | ✅ (port 80)  | ✅ | Works, but traffic rides CF port 80 — prefer WS+TLS |
| VMess + **WS** + TLS                             | ✅ | ✅ | Same as VLESS+WS |
| VMess / VLESS + **gRPC** + TLS                   | ✅ specialized | ✅ | CF supports gRPC over HTTP/2, but **Network → gRPC** must be enabled in the dashboard; slower handshake than WS |
| VLESS/VMess/Trojan over plain **TCP** (no WS/gRPC) | ❌ | ✅ | CF does not pass arbitrary TCP; clients connect to your VPS IP directly |
| Trojan + TLS (streaming over TCP)                | ❌ | ✅ | Needs raw TLS; CF cannot relay it — use DNS-only |
| **Reality** (any transport)                      | ❌ | ✅/⚠️ | Reality must be **the only** TLS on the path; CF's edge TLS breaks the fingerprint — use DNS-only and never enable CF's proxy on this hostname |
| **Shadowsocks** (TCP/UDP)                        | ❌ | ✅ | Raw TCP/UDP passthrough only; orange cloud will not work |
| HTTP/2-only transports, KCP, QUIC                | ❌ | ⚠️ (QUIC needs a 443/udp open firewall) | Not HTTP-relayable on CF free plan |

Practical port notes for the orange cloud (free plan): CF proxies only fixed
ports — HTTP `80, 8080, 2052, 2082, 2086, 2095`, HTTPS `443, 2053, 2083,
2087, 2096, 8443`. Route your WS inbound to one of those and set the matching
port in the client share link.

Panel pointing tips:

1. If a hostname looks like a normal website (Reality/SS/plain TCP) -> set
   the record to **DNS only (grey)**; TLS terminates on your VPS.
2. Keep the **panel dashboard itself** always behind the orange cloud —
   https://panel.example.com — so the real VPS IP stays hidden.
3. The panel's share links use the host from the admin's browser request;
   for CF-proxied clients add the proxied hostname (WS+TLS) so the QR code
   carries the same address clients should dial.
