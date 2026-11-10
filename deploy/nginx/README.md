# nginx setup for the Xray panel (Phase 7)

The panel listens on `127.0.0.1:8080` only. nginx on port 443 (HTTPS) is the
single public entry, so the panel port stays closed to the internet.

## 1. Activate the site

```bash
# 1) copy and personalize the template
sudo cp deploy/nginx/xray-panel.conf /etc/nginx/sites-available/xray-panel
sudo nano /etc/nginx/sites-available/xray-panel
#    -> replace PANEL_DOMAIN, CERT_PATH, KEY_PATH with your values

# 2) websocket map (http context) - enables proxied WS if needed later
sudo tee /etc/nginx/conf.d/xray-panel-map.conf <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF

# 3) enable + verify + reload
sudo ln -s /etc/nginx/sites-available/xray-panel /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 2. Get a certificate

- **Let's Encrypt (recommended for DNS-only domains):**
  ```bash
  sudo apt install certbot python3-certbot-nginx -y
  sudo certbot certonly --nginx -d panel.example.com
  # renewal is automatic via systemd timer, check: certbot renew --dry-run
  ```
- **Cloudflare Origin Certificate (recommended behind the orange cloud):**
  1. Cloudflare Dashboard -> SSL/TLS -> Origin Server -> Create Certificate
     (RSA 2048, hostnames: `panel.example.com`, validity up to 15 years).
  2. Save the pieces on the server:
     ```bash
     sudo mkdir -p /etc/nginx/ssl
     sudo nano /etc/nginx/ssl/panel-origin.pem   # paste the Origin Certificate
     sudo nano /etc/nginx/ssl/panel-origin.key   # paste the Private Key
     sudo chmod 600 /etc/nginx/ssl/panel-origin.key
     ```
  3. On Cloudflare set SSL/TLS mode to **Full (strict)**.
  4. In the vhost use `ssl_certificate /etc/nginx/ssl/panel-origin.pem;`
     and `ssl_certificate_key /etc/nginx/ssl/panel-origin.key;`

## 3. Real visitor IP behind Cloudflare (optional but nice)

Behind Cloudflare's proxy, `$remote_addr` is a Cloudflare edge IP (172.70.x.x).
Either restore real visitor IPs:

```bash
# /etc/nginx/conf.d/cloudflare-real-ip.conf
# trusted proxies: every Cloudflare edge IP (get current lists at
# https://www.cloudflare.com/ips/ and review them from time to time)
sudo tee /etc/nginx/conf.d/cloudflare-real-ip.conf <<'EOF'
# Example lines (complete them with the current published ranges):
# set_real_ip_from 173.245.48.0/20;
# set_real_ip_from 103.21.244.0/22;
# ... (any IPv6 ranges you need)
# real_ip_header CF-Connecting-IP;
EOF
```

Or simply rely on the dashboard's Persian logs and skip it. The panel also
honors `X-Forwarded-Proto` for building subscription URLs, already sent.

## 4. Keep the panel port closed (important)

`deploy/install.sh` binds the panel service with
`--host 127.0.0.1`, i.e. unreachable from the internet without nginx. To also
block it at the firewall level (defense in depth):

```bash
# ufw (Ubuntu/Debian default firewall)
sudo ufw default deny incoming
sudo ufw allow OpenSSH            # or your SSH port
sudo ufw allow 80,443/tcp         # only nginx is public
sudo ufw enable
sudo ufw status verbose           # 8080 must NOT appear

# verify nothing is listening publicly (expect 127.0.0.1:8080):
sudo ss -tlnp | grep 8080
curl -m 3 http://<SERVER_PUBLIC_IP>:8080/   # must fail / timeout
```

On other distros: keep a default-deny policy (`nft`, `firewalld`) or skip the
blocklist as long as `ss -tlnp | grep 8080` shows it bound to `127.0.0.1`.
