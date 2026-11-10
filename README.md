# Xray Management Panel

A self-hosted web panel for managing Xray-core inbounds, clients, traffic, and config links —
similar in spirit to 3x-ui, built with Python (FastAPI) and SQLite.

## Tech stack

- Backend: FastAPI + SQLAlchemy (SQLite)
- Frontend: Jinja2 templates + vanilla CSS (clean, responsive, RTL/فارسی-friendly)
- Deployment target: Linux (Ubuntu/Debian) under systemd (documented in later phases)

## Current status — Phase 7 complete (deploy tooling; all app phases shipped)

- Phase 1: project structure, SQLite + `Admin` model, bcrypt, session auth, CSRF
- Phase 2: **Inbound management + Xray config.json generation**
  - Create / edit / delete / enable / disable inbounds
  - Protocols: VLESS, VMess, Trojan, Shadowsocks
  - Transports: TCP, WebSocket, gRPC
  - Security: none, TLS, Reality
  - Full field validation (`app/validators.py`): port range + uniqueness,
    Reality requires dest/serverNames/privateKey, TLS requires cert+key paths,
    SS requires cipher + password
  - Config pipeline (`app/xray_gen.py`): active DB rows → `config.json` —
    validated (JSON parse, plus `xray run -test` when the binary exists) —
    applied atomically via `os.replace`, previous config saved as `.bak`
    rollback copy; failures leave the live config untouched
- Phase 3 (new): **Client management + share links + QR codes**
  - Clients inside an inbound: auto-generated UUID (trojan password),
    optional VLESS flow, traffic limit (GB), expiry date, IP limit,
    enable/disable, reset-traffic usage counters (consumed in Phase 4)
  - Client emails unique per inbound (required by the Xray stats API)
  - Shadowsocks inbounds are single-credential in Xray; the panel shows the
    single `ss://` link instead of client rows
  - Config generation emits only enabled clients, in each protocol's shape
    (vless `id`, vmess `id`+`alterId: 0`, trojan `password`)
  - Share links (host = your browsing Host header): `vless://`, `vmess://`
    (base64 JSON), `trojan://`, `ss://`; Reality links carry `sni`/`pbk`/`sid`,
    WS links `path`, gRPC links `serviceName`
  - QR codes: inline SVG data URIs (no PIL/PNG dependency)
- Phase 4 (new): **Traffic monitoring + auto-disable**
  - Generated config gains the Xray gRPC stats API when
    `XRAY_STATS_ENABLED=1`: a localhost `dokodemo-door` "api" inbound
    (default `127.0.0.1:23231`), plus `stats`, `api`, `policy` sections and a
    routing rule that isolates api traffic from users
  - `app/xray_stats.py`: minimal gRPC client over HTTP/2 (`h2`) with
    hand-rolled protobuf for `StatsService/GetQueryStats` (no generated stubs)
  - `app/monitor.py`: sweeps `user>>>email>>>uplink|downlink` counters with
    `reset=True` and accumulates exact deltas into the database
  - Auto-disable rules (client.left `disabled_reason` badge):
    * `traffic_limit` — used bytes reach `total_gb`
    * `expired` — the day after `expire_at` (expiry day itself still works)
    * `manual` — set by admin toggle/enabling
  - The monitor never resurrects a disabled client, even after a traffic
    reset; only an admin toggle/re-enable clears `disabled_reason`
  - Client mail/email must now be unique **panel-wide** (Xray stats keys
    carry no inbound id); duplicate submissions are rejected with a message
  - **به‌روزرسانی ترافیک** button on the client list triggers a sweep
    (`POST /system/monitor`, CSRF-protected, with sanitized `return_to`);
    optionally automated via `MONITOR_INTERVAL_SECONDS` background loop
  - Note: xray is *not* restarted by the panel; re-apply config.json and
    restart xray separately after adding the new api inbound (systemd phase 5)
- Phase 5 (new): **Dashboard + Xray service control**
  - Live dashboard (`/dashboard`): CPU / RAM / disk / swap cards with
    progress bars (psutil), uptime + boot time
  - Traffic aggregates: total inbounds (and active), total users (and
    active), disabled-by-traffic and disabled-by-expiry counts, total used GB
  - Xray service card: live systemd status (`is-active`) with one-click
    **start / restart / stop** (`POST /system/xray/{action}`, whitelisted
    verbs, no shell, CSRF-protected; unit name from `XRAY_SERVICE_NAME`)
  - Graceful degradation on hosts without systemd: badge shows
    «غیرقابل دسترسی», control attempts flash a Persian error  - "به‌روزرسانی ترافیک" button also available from the dashboard
    (triggers the Phase 4 sweep with sanitized `return_to=/dashboard`)
- Phase 6 (new, final): **Subscriptions + Backup/Restore + Telegram + deploy**
  - Subscription URLs: every client gets an unguessable
    `http(s)://<host>/sub/<token>` (token generated lazily, shown on the
    client list + view page with QR). Anonymous GET returns base64 of the
    client's share links (`vless://...` etc), ready for v2rayNG/Nekoray /
    Streisand; unknown token → 404
  - Idempotent SQLite column migration on startup (`sub_token`,
    `disabled_reason`, `panel_settings` table) so existing DBs upgrade in
    place — `create_all` never adds columns
  - Backup: `POST /backup/download` (CSRF) streams inbounds + clients
    (counters included; admin passwords NOT included) as a JSON attachment
  - Restore: `POST /backup/restore` (CSRF + admin, 4 MiB cap) validates the
    file (structure, port range, unique emails) and replaces all
    inbounds/clients; admin rows untouched so you cannot lock yourself out
  - Telegram bot: CLI-run `python -m app.telegram_bot` (stdlib urllib,
    long-polling, no webhook/ports). `/status` `/traffic` `/expired` `/help`;
    first `/start` registers the owner chat (persisted in `panel_settings`);
    other chats rejected. Disabled when `TELEGRAM_BOT_TOKEN` is empty
  - Deploy: `deploy/install.sh [--with-ssl <cert> <key>] [--with-telegram]`
    creates a dedicated system user, venv, randomized SECRET_KEY `.env`,
    systemd unit `xray-panel.service` (+ optional bot unit)
  - HTTPS: either uvicorn `--ssl-certfile/--ssl-keyfile` flags (wired by the
    installer) or a reverse proxy (nginx/Caddy example in install notes)
- Phase 7 (new): **Deployment support**
  - `deploy/nginx/xray-panel.conf` — reverse-proxy vhost template (certbot
    or Cloudflare Origin Certificate) that fronts the panel; nginx is the
    only public entry
  - Panel service binds `127.0.0.1` by default (installer override
    `PANEL_HOST`) + firewall recipe in `deploy/nginx/README.md` so port 8080
    stays closed to the internet
  - `.gitignore` now excludes `.env`, databases, TLS keys, and runtime
    artifacts
  - `.github/workflows/deploy.yml` — on every push to `main` it SSHes to the
    VPS (GitHub Secrets: `SSH_HOST`/`SSH_USER`/`SSH_PRIVATE_KEY` [+ optional
    `SSH_PORT`/`DEPLOY_DIR`]), `git reset --hard origin/main`,
    `pip install -r requirements.txt`, restarts `xray-panel.service`
  - `docs/setup-guide.md` — non-programmer walk-through: VPS + SSH key,
    install.sh, Cloudflare DNS record, HTTPS, GitHub Secrets, first deploy
  - `docs/cloudflare-inbounds.md` — which inbound shapes work behind the
    orange cloud (WS/gRPC+TLS) vs which need DNS-only (Reality, Trojan,
    Shadowsocks, plain TCP)

## Local setup

### 1. Create a virtual environment and install dependencies

```bash
cd xray-panel
python3 -m venv venv
source venv/bin/activate            # Linux
# venv\Scripts\activate             # Windows
pip install -r requirements.txt
```

> **If PyPI is unreachable on your network** (e.g. filtering), use a mirror:
> ```bash
> pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

- Set `SECRET_KEY` to a strong random value: `openssl rand -hex 32`
- Set `DEFAULT_ADMIN_USERNAME` and `DEFAULT_ADMIN_PASSWORD` (min 8 chars) —
  used only to create the admin account on first startup. Change the password
  immediately after the first login.
- Optionally set `PANEL_PORT` (default `8080`) and `PANEL_PATH` (default `/`,
  change to e.g. `/panel` to add a secret URL prefix)

### 3. Run

```bash
python run.py
```

Then open `http://localhost:8080/` — it redirects to the login page.

## How to test Phase 1 (checklist)

۱. Start the server then: login → dashboard → change-password → logout chain.
Or run the automated suite:

```bash
python tests_smoke.py     # Phase 1: auth flow
python tests_phase2.py    # Phase 2: inbound CRUD + config generation
python tests_phase3.py    # Phase 3: clients + share links + QR
python tests_phase4.py    # Phase 4: stats + auto-disable
python tests_phase5.py    # Phase 5: dashboard + xray control
python tests_phase6.py    # Phase 6: subscription + backup + telegram
```

### Phase 2 manual checklist

1. Log in → topbar → **اینباند‌ها**.
2. **+ اینباند جدید**: create a VLESS inbound (e.g. port 40001, tcp/none).
3. Try a **duplicate port** (e.g. 40001 again) → form rejects with a message.
4. Try **Reality** without dest/server names/private key → rejected.
5. Fill Reality fields (`xray x25519` produces the private key on Linux) → saved.
6. Toggle disable → badge changes; the disabled inbound is excluded from config.
7. **اعمال کانفیگ**: generates + validates + applies `XRAY_CONFIG_PATH`
   (from `.env`), creating a `.bak` rollback copy first time over.
8. Check the generated `config.json` on disk.
9. Delete → remove from the table, then apply again.

### Phase 3 manual checklist

1. Log in → **اینباند‌ها** → open one inbound's **کاربران**.
2. **+ کاربر جدید**: leave the UUID empty (auto-generated), set a traffic
   limit and expiry, save.
3. Duplicate the same client email → rejected with a message.
4. The list shows a `vless://` (etc.) link and a QR code per client.
5. Toggle/disable a client, **اعمال کانفیگ** from the inbounds page, and
   check the generated `config.json` — disabled clients are not written.
6. Reset traffic zeroes the used-bytes counters.
7. For Reality inbounds put the public key (`xray x25519` public part) in
   the inbound's Reality settings so `pbk` appears in share links.

### Phase 4 manual checklist

1. On the server, re-apply config and restart Xray once so the new
   localhost api inbound (port `XRAY_API_PORT`, default 23231) is live:
   `xray run -c /usr/local/etc/xray/config.json` (or restart the service).
2. Log in → **اینباند‌ها** → open a client list → click
   **به‌روزرسانی ترافیک**: usage numbers fill in (GB), disabled-by-limit
   clients get their badge.
3. Create a client with `total_gb = 1`, generate some traffic, refresh —
   when usage reaches 1 GB the client auto-disables
   (badge «غیرفعال (اتمام ترافیک)»).
4. Set `expire_at` to a past date and refresh → badge
   «غیرفعال (منقضی)»; the expiry day itself is still usable.
5. After a reset-traffic the client stays disabled — re-enable with a
   toggle only (the monitor would otherwise flip it straight back).
6. Optional: set `MONITOR_INTERVAL_SECONDS=60` in `.env` and restart the
   panel — traffic now updates every minute without clicking.
7. Client emails must be unique across inbounds; try creating the same
   email on another inbound — the form rejects it.

### Phase 5 manual checklist

1. Log in → **داشبورد**: CPU/RAM/disk/swap cards fill in live values,
   uptime and boot time show at the bottom of the card.
2. The **سرویس Xray** card shows the current systemd state; on the server
   press **start/restart/stop** and confirm the state badge flips.
   (On Windows dev machines the badge reads «غیرقابل دسترسی».)
3. The traffic summary shows inbound/user counts, disabled counters and
   total used GB; **به‌روزرسانی ترافیک** refreshes from the Xray stats API.
4. Kill xray on the server (`systemctl stop xray`) and check the sweep —
   the dashboard flashes a Persian error instead of crashing.
5. Set `XRAY_SERVICE_NAME` in `.env` if your unit is not called `xray`.

### Phase 6 manual checklist

1. **Subscriptions**: clients page → the «اشتراک — <کاربر>» grid shows a
   `/sub/<token>` URL + QR; scan/import it into v2rayNG → the client's
   config appears. Opening the link directly shows base64 text.
2. Fetch `/sub/<token>/view` in a browser for a human-readable page.
3. **Backup**: dashboard → **پشتیبان‌گیری و بازیابی** → **دانلود پشتیبان**,
   check the JSON (inbounds+clients; no admin passwords inside).
4. **Restore**: change a remark in the file, restore, confirm Persian
   flash + panels replaced; submitting garbage JSON keeps the panel intact.
5. **Telegram** (server): put a @BotFather token in `.env` →
   `venv/bin/python -m app.telegram_bot` → send `/start` from your chat,
   then `/status`, `/traffic`, `/expired`. Any other chat is rejected.
6. **Install**: `sudo bash deploy/install.sh --with-telegram` on a fresh
   server; then set `DEFAULT_ADMIN_PASSWORD`, restart the unit, browse
   `http://<ip>:8080`; add `--with-ssl <cert> <key>` (or an nginx/Caddy
   reverse proxy) for HTTPS.

Note: on dev machines without the `xray` binary installed, validation falls
back to JSON-syntax checking only; on the server it runs the real
`xray run -test` before replacing the live config.

## Project structure

```
xray-panel/
├── app/
│   ├── main.py          # FastAPI app: middleware, static, routers, startup
│   ├── config.py        # Settings loaded from .env (never hardcode secrets)
│   ├── database.py      # SQLAlchemy engine/session + init_db()
│   ├── models.py        # ORM models: Admin, Inbound (Ph.2), Client (Ph.3-6), PanelSetting
│   ├── validators.py    # Inbound + client field validation
│   ├── xray_gen.py      # config.json generation, validation, safe apply
│   ├── xray_stats.py    # minimal gRPC stats client (h2 + hand-rolled protobuf)
│   ├── monitor.py       # traffic sweeps + auto-disable (Phase 4)
│   ├── system_stats.py  # psutil snapshots for the dashboard (Phase 5)
│   ├── xray_control.py  # systemctl start/stop/restart + is-active (Phase 5)
│   ├── telegram_bot.py  # CLI-run Telegram bot, stdlib long-polling (Phase 6)
│   ├── share_links.py   # share links + QR SVG builders (Phase 3)
│   ├── schemas.py       # Pydantic request/response schemas with validation
│   ├── security.py      # bcrypt hashing, JWT encode/decode, CSRF token helpers
│   ├── crud.py          # Admin + Inbound + Client CRUD
│   ├── dependencies.py  # get_current_admin, CSRF verification dependency
│   └── routers/
│       ├── auth.py      # /login, /logout, /change-password
│       ├── admin.py     # /dashboard + /system/xray/{action} (Phase 5)
│       ├── helpers.py   # CSRF / URL / flash helpers
│       ├── inbounds.py  # /inbounds CRUD + /inbounds/apply
│       ├── clients.py   # /inbounds/{id}/clients CRUD + share/QR (Phase 3)
│       ├── monitor.py   # POST /system/monitor traffic sweep (Phase 4)
│       ├── subscription.py # public GET /sub/{token}[/view] (Phase 6)
│       └── backup.py    # POST /backup/download + /backup/restore (Phase 6)
│   ├── base.html, login.html, dashboard.html
│   ├── inbounds/        # list.html, form.html (Phase 2)
│   └── clients/         # list.html, form.html (Phase 3) + subscription.html
├── static/css/style.css # RTL-friendly styles
├── deploy/
│   ├── install.sh        # one-line installer + systemd units (+ssl/-bot)
│   └── nginx/
│       ├── xray-panel.conf # reverse proxy vhost template (Phase 7)
│       └── README.md       # activation + certbot/CF origin + firewall
├── docs/
│   ├── setup-guide.md      # end-to-end non-programmer walk-through
│   └── cloudflare-inbounds.md # orange vs grey cloud per inbound type
├── .github/workflows/deploy.yml # auto-deploy on push to main (SSH + systemd)
├── .env.example         # environment template
├── .gitignore
├── requirements.txt
├── run.py               # uvicorn entry point
├── tests_smoke.py       # Phase 1 automated tests
├── tests_phase2.py      # Phase 2 automated tests
└── README.md
```

## Security notes

- Passwords: bcrypt with per-hash salt.
- Auth: JWT (HS256, 24h expiry) in an HttpOnly cookie; sessions signed with `SECRET_KEY`.
- CSRF: per-session token, `constant-time` compared; CSRF token rotated on login and password change.
- SQL injection: SQLAlchemy ORM parameterized queries everywhere.
- XSS: Jinja2 auto-escaping is on for HTML templates.
- Secrets stay out of git: `.env`, databases and TLS keys are gitignored;
  the deploy workflow's SSH private key lives in GitHub Secrets only.
- The admin bootstrap password comes from `.env`, never hardcoded in source.
