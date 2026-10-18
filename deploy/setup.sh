#!/usr/bin/env bash
# Runs ON the server: bash applier/deploy/setup.sh   (safe to re-run after copying new code).
# Installs Python deps, the schedules, and an HTTPS dashboard. Works on Amazon Linux 2023 (dnf)
# and Ubuntu/Debian (apt). HTTPS uses <ip>.sslip.io so you don't need to own a domain.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"   # wherever the folder was copied
USER_NAME="$(whoami)"

# Things that hold secrets / personal data and must be copied over by hand.
for f in .env profile/resume.md; do
  [[ -f "$APP/$f" ]] || { echo "Missing $f. Copy it to the server first (see README)."; exit 1; }
done
grep -q '^DASHBOARD_PASSWORD=.\+' "$APP/.env" || { echo "Set DASHBOARD_PASSWORD in .env"; exit 1; }
mkdir -p "$APP/data"

# ---------------------------------------------------------------- Python 3.10+
# Amazon Linux 2023 ships Python 3.9 as python3, which is too old; it offers 3.11/3.12 alongside.
pick_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null && "$p" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

if command -v dnf >/dev/null; then
  PY="$(pick_python)" || {
    sudo dnf install -y python3.12 python3.12-pip || sudo dnf install -y python3.11 python3.11-pip
    PY="$(pick_python)"
  }
elif command -v apt-get >/dev/null; then
  PY="$(pick_python)" || {
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv python3-pip
    PY="$(pick_python)"
  }
else
  echo "Unsupported OS: need dnf (Amazon Linux) or apt (Ubuntu)."; exit 1
fi
echo "Using $($PY --version)"

# Rebuild the venv if it was made by a different Python (e.g. copied from a Mac).
if [[ ! -x "$APP/.venv/bin/python" ]] || ! "$APP/.venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  rm -rf "$APP/.venv"
  "$PY" -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

sudo timedatectl set-timezone Asia/Kolkata

# ---------------------------------------------------------------- Caddy (HTTPS)
if ! command -v caddy >/dev/null; then
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y -qq caddy        # Ubuntu's package brings its own service
  else
    # Not packaged for Amazon Linux: install the official static binary and our own service.
    case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; *) echo "Unknown CPU $(uname -m)"; exit 1 ;; esac
    curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=$ARCH" -o /tmp/caddy
    sudo install -m 755 /tmp/caddy /usr/local/bin/caddy && rm /tmp/caddy
    id caddy >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/caddy --shell /sbin/nologin caddy
    sudo mkdir -p /var/lib/caddy /etc/caddy /etc/systemd/system && sudo chown caddy:caddy /var/lib/caddy
    sudo tee /etc/systemd/system/caddy.service >/dev/null <<'EOF'
[Unit]
Description=Caddy (HTTPS for the Applier dashboard)
After=network-online.target
Wants=network-online.target

[Service]
User=caddy
Group=caddy
Environment=XDG_DATA_HOME=/var/lib XDG_CONFIG_HOME=/etc
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
  fi
fi

# Caddy fetches a Let's Encrypt certificate for <ip>.sslip.io by itself (needs ports 80 + 443 open).
IP="$(curl -fsS https://checkip.amazonaws.com)"
HOSTNAME_="${IP//./-}.sslip.io"
sudo mkdir -p /etc/caddy
echo "$HOSTNAME_ {
    reverse_proxy 127.0.0.1:5050
}" | sudo tee /etc/caddy/Caddyfile >/dev/null

# ---------------------------------------------------------------- services
# systemd units, with this user's name and paths filled in
for unit in "$APP"/deploy/systemd/*; do
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@APP@|$APP|g" "$unit" | sudo tee "/etc/systemd/system/$(basename "$unit")" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now applier-dashboard.service applier-watch.timer applier-daily.timer caddy.service
sudo systemctl restart applier-dashboard.service caddy.service

# Fresh install with no drafts yet: run both jobs now instead of waiting for the timers.
DRAFTS="$("$APP/.venv/bin/python" -c "
import sqlite3, sys
try: print(sqlite3.connect(sys.argv[1]).execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
except Exception: print(0)" "$APP/data/applier.db")"
if [[ "$DRAFTS" == "0" ]]; then
  sudo systemctl start --no-block applier-watch.service applier-daily.service
  echo "Started a first scout + drafting run (takes ~10-15 min). Follow it: journalctl -fu applier-daily"
fi

echo
echo "Done. Dashboard: https://$HOSTNAME_  (user: anything, password: DASHBOARD_PASSWORD from .env)"
echo "If it doesn't load, open ports 80 and 443 in the EC2 security group."
echo "Schedules:"; systemctl list-timers 'applier-*' --no-pager | head -4
