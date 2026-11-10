#!/usr/bin/env bash
# One-line installer for the Xray management panel (Phase 6).
#
# Usage (as root on a fresh Debian/Ubuntu server):
#   bash install.sh                # panel only
#   bash install.sh --with-telegram   # also enable the Telegram bot unit
#   bash install.sh --with-ssl /path/fullchain.pem /path/privkey.pem
#
# What it does:
#   1. Installs system packages (python3-venv etc.) via apt (if available)
#   2. Creates a venv and installs requirements.txt
#   3. Writes a .env with a strong random SECRET_KEY (never committed)
#   4. Installs systemd units for the panel (and optional bot)
#   5. Leaves setting your admin password + xray install as explicit steps
set -euo pipefail

TARGET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PANEL_USER="${PANEL_USER:-xraypanel}"
# 127.0.0.1 = nginx fronts the panel (deploy/nginx/) — port stays closed.
# Override with PANEL_HOST=0.0.0.0 only if you accept a publicly open panel.
PANEL_HOST="${PANEL_HOST:-127.0.0.1}"
PANEL_PORT="${PANEL_PORT:-8080}"
SERVICE_USER_INSTALL="${SERVICE_USER_INSTALL:-1}"

SSL_ARGS=""
WITH_TELEGRAM=0
if [[ "${1:-}" == "--with-ssl" ]]; then
    CERT="$2"; KEY="$3"
    SSL_ARGS="--ssl-certfile ${CERT} --ssl-keyfile ${KEY}"
fi
if [[ "${1:-}" == "--with-telegram" || "${2:-}" == "--with-telegram" || "${3:-}" == "--with-telegram" ]]; then
    WITH_TELEGRAM=1
fi

echo "==> panel directory: ${TARGET_DIR}"

# 1. system packages (best-effort; works on any distro with python3+venv)
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq || true
    apt-get install -y -qq python3 python3-venv curl >/dev/null || true
fi

# dedicated unprivileged user
if [[ "$SERVICE_USER_INSTALL" == "1" ]] && ! id "$PANEL_USER" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin "$PANEL_USER"
    echo "==> created system user: ${PANEL_USER}"
fi

cd "$TARGET_DIR"

# 2. venv + dependencies
python3 -m venv venv
./venv/bin/pip install --upgrade pip >/dev/null
./venv/bin/pip install -r requirements.txt
echo "==> python environment ready"

# 3. .env with a random secret (do not echo it)
if [[ ! -f .env ]]; then
    cat > .env <<ENV
XRAY_PANEL_ENV=production
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
PANEL_PORT=${PANEL_PORT}
PANEL_PATH=/
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=
DATABASE_URL=sqlite:///${TARGET_DIR}/xray_panel.db
XRAY_BIN_PATH=/usr/local/bin/xray
XRAY_CONFIG_PATH=/usr/local/etc/xray/config.json
XRAY_SERVICE_NAME=xray
XRAY_STATS_ENABLED=1
XRAY_API_HOST=127.0.0.1
XRAY_API_PORT=23231
MONITOR_INTERVAL_SECONDS=60
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENV
    chmod 600 .env
    echo "==> wrote .env (SECRET_KEY randomized; set DEFAULT_ADMIN_PASSWORD before first run!)"
fi

chown -R "$PANEL_USER":"$PANEL_USER" "$TARGET_DIR" 2>/dev/null || true

# 4. systemd units
cat > /etc/systemd/system/xray-panel.service <<UNIT
[Unit]
Description=Xray Management Panel (FastAPI)
After=network.target

[Service]
Type=simple
User=${PANEL_USER}
WorkingDirectory=${TARGET_DIR}
# --host 127.0.0.1 by default: the panel must never be exposed directly;
# nginx (deploy/nginx/xray-panel.conf) is the only public entry point.
# Override with PANEL_HOST=0.0.0.0 ONLY if you consciously want it open.
ExecStart=${TARGET_DIR}/venv/bin/uvicorn app.main:app --host ${PANEL_HOST} --port ${PANEL_PORT} ${SSL_ARGS}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

if [[ "$WITH_TELEGRAM" == "1" ]]; then
cat > /etc/systemd/system/xray-panel-telegram.service <<UNIT
[Unit]
Description=Xray Panel Telegram bot (long polling)
After=network.target xray-panel.service

[Service]
Type=simple
User=${PANEL_USER}
WorkingDirectory=${TARGET_DIR}
ExecStart=${TARGET_DIR}/venv/bin/python -m app.telegram_bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now xray-panel-telegram.service
echo "==> telegram bot unit installed"
else
    systemctl daemon-reload
fi

systemctl enable --now xray-panel.service

echo ""
echo "==> done. Next steps:"
echo "    1. Install xray itself (official script):"
echo "       bash -c \"\$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install"
echo "    2. Set a strong DEFAULT_ADMIN_PASSWORD in .env, 'systemctl restart xray-panel'"
echo "    3. Front the panel with nginx (HTTPS) and close the panel port:"
echo "       see deploy/nginx/README.md - panel listens on 127.0.0.1:${PANEL_PORT} only."
echo "    4. Then browse https://<panel-domain>/ (or http://<server-ip>:${PANEL_PORT} for local-only testing)."
