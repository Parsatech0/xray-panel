# Setting up the Xray panel end-to-end (non-programmer guide)

Everything below is copy-paste friendly. You need only three things:

1. **A VPS** — any $3-5/mo server (Ubuntu 22.04 suggested) with its **IP address**
2. **A domain** on Cloudflare (free plan is enough), e.g. `example.com`
3. **A GitHub repository** of this project (push this folder to GitHub first:
   GitHub Desktop, or `git init && git add . && git commit && git push`)

> Replace `panel.example.com` with your real subdomain everywhere below.
> Replace `<VPS-IP>` with the server's public IP.

---

## Step 1 — create the server + an SSH login (one time)

Open **PowerShell** on Windows (or terminal on macOS/Linux) and create a key pair:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/vps_key -C "panel deploy"
# press Enter twice (no pass-phrase). Two files are created:
#   ~/.ssh/vps_key      -> PRIVATE key — goes into GitHub Secrets only. NEVER share it.
#   ~/.ssh/vps_key.pub  -> PUBLIC  key — goes on the server.
```

Get the public key text to paste on the server:

```bash
type ~/.ssh/vps_key.pub      # Windows      (Linux: cat ~/.ssh/vps_key.pub)
```

Log into the raw VPS **with the password your provider emailed you**:

```bash
ssh root@<VPS-IP>            # first time: password auth is fine
mkdir -p ~/.ssh && echo "PASTE-PUBLIC-KEY-LINE-HERE" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```

From now on `ssh root@<VPS-IP>` logs in without a password.

## Step 2 — run the installer on the VPS (one time)

```bash
# on the VPS, as root:
apt update
apt install -y git
git clone https://github.com/YOUR-GITHUB-NAME/YOUR-REPO.git /opt/xray-panel
cd /opt/xray-panel
bash deploy/install.sh        # adds --with-telegram if you want the bot
```

Then open the config and set a strong admin password:

```bash
nano /opt/xray-panel/.env     # DEFAULT_ADMIN_PASSWORD=YourStrongPass123!
systemctl restart xray-panel
```

## Step 3 — connect the domain on Cloudflare

1. Cloudflare Dashboard → your domain (`example.com`) → **DNS** → **Add record**:
   - Type `A`, Name `panel`, IPv4 address `<VPS-IP>` →
   - **Proxy status: orange cloud (Proxied)** — turbo-charged & hides the IP.
     (If you *skip* Cloudflare's proxy but want pure TLS, set **grey cloud
     / DNS only** — see the reconciliation table later.)
2. **SSL/TLS → Overview → set mode to “Full (strict)”**.
3. **SSL/TLS → Edge Certificates** → enable “Always Use HTTPS”.

## Step 4 — nginx + HTTPS on the VPS

Follow **`deploy/nginx/README.md`** — 4 short commands a) copy nginx template,
b) create the certificate (Cloudflare Origin Cert for orange-cloud, or certbot),
c) `nginx -t && systemctl reload nginx`, d) close the panel port with ufw.
After this, **https://panel.example.com opens the login page**.

Log in → داشبورد → **تغییر رمز عبور** (change-password) and replace the
default password now.

## Step 5 — add the GitHub Secrets (for auto-deploy)

Open your repo on GitHub → **Settings → Secrets and variables → Actions →
“New repository secret”** — repeat for each row:

| Secret name      | Value to paste                                                        |
|------------------|------------------------------------------------------------------------|
| `SSH_HOST`       | `panel.example.com` (or `<VPS-IP>`)                                    |
| `SSH_USER`       | `root` (or the dedicated deploy user from install.sh)                  |
| `SSH_PRIVATE_KEY`| FULL contents of `~/.ssh/vps_key` — every line incl. `BEGIN/END` lines  |
| `SSH_PORT`       | `22` (skip if default)                                                 |
| `DEPLOY_DIR`     | `/opt/xray-panel` (skip if default)                                    |

Allow the service restart with passwordless sudo (skip entirely when you
picked `root` as `SSH_USER`):

```bash
# on the VPS:
sudo visudo -f /etc/sudoers.d/xray-panel-deploy
# paste this single line - replace <SSH_USER> with the user from the secrets:
<SSH_USER> ALL=(root) NOPASSWD: /usr/bin/systemctl restart xray-panel.service
```

## Step 6 — deploy: push to `main`

`git add . && git commit -m "deploy" && git push` — then:

1. Repo → **Actions** tab → “deploy” workflow → a green ✅ when done (~40 s).
   A red ❌ → open the log; the failing line is highlighted.
2. Refresh **https://panel.example.com** — your change is live.

Done! Daily use is just: commit → push → green check.

---

## Troubleshooting map

| Symptom | First thing to check |
|---|---|
| Actions job fails at ssh | Secret names/typos, `SSH_PORT`, is the VPS firewall open for 22? |
| Job fails at `git reset` | `DEPLOY_DIR` correct? Repo cloned there? Remote named `origin`? |
| Job fails at `sudo systemctl` | Missing the sudoers line from Step 5 (or not root user) |
| Panel shows “connection refused” | `systemctl status xray-panel` on the VPS; check `.env` syntax |
| Domain unresolved / TLS errors | Cloudflare DNS record, SSL mode, nginx config paths |

Security notes: `SSH_PRIVATE_KEY` secret never leaves GitHub's vault; every
run recreates nothing, it only syncs code and restarts one service. `.env`
on the VPS is never overwritten by deploys.
