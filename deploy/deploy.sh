#!/usr/bin/env bash
# Copy Applier to a fresh Ubuntu server and (re)install it. Run from your laptop:
#   deploy/deploy.sh ubuntu@1.2.3.4 [path/to/ssh-key.pem]
# Safe to re-run: it updates code and profile, and never overwrites the server's database.
set -euo pipefail
HOST="${1:?usage: deploy/deploy.sh user@host [ssh-key]}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
[[ -n "${2:-}" ]] && SSH_OPTS+=(-i "$2")
cd "$(dirname "$0")/.."

grep -q '^DASHBOARD_PASSWORD=.\+' .env || { echo "Add DASHBOARD_PASSWORD=... to .env first"; exit 1; }

rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude .venv --exclude data --exclude __pycache__ --exclude .idea --exclude .git \
  ./ "$HOST:applier/"
# First deploy only: carry over the local database so existing drafts and statuses come along.
ssh "${SSH_OPTS[@]}" "$HOST" 'test -f applier/data/applier.db' || {
  ssh "${SSH_OPTS[@]}" "$HOST" 'mkdir -p applier/data'
  [[ -f data/applier.db ]] && rsync -az -e "ssh ${SSH_OPTS[*]}" data/applier.db data/gcc_manual.csv "$HOST:applier/data/"
}
ssh "${SSH_OPTS[@]}" "$HOST" 'bash applier/deploy/setup.sh'
