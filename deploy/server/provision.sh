#!/usr/bin/env bash
# Provision the venue-arb dashboard server (fresh Ubuntu 24.04, run as root; safe to re-run).
#   sudo REPO_URL=<git url> bash provision.sh /path/to/optiplex.pub [domain]
# REPO_URL is needed only on the first run (when /srv/venue-arb/app has no checkout; a hand-copied one is fine); REPO_BRANCH defaults to main.
set -euo pipefail

PUBKEY_FILE=${1:?usage: provision.sh /path/to/optiplex.pub [domain]}
DOMAIN=${2:-}
BRANCH=${REPO_BRANCH:-main}
BASE=/srv/venue-arb
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -r $PUBKEY_FILE ]] || { echo "cannot read $PUBKEY_FILE" >&2; exit 1; }
PUBKEY=$(cat "$PUBKEY_FILE")
[[ $PUBKEY == ssh-ed25519\ * ]] || { echo "$PUBKEY_FILE is not an ssh-ed25519 public key" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq rsync git curl ufw cron debian-keyring debian-archive-keyring apt-transport-https gpg

# Caddy from its official apt repo
if ! command -v caddy >/dev/null; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

# uv, system-wide
if ! command -v uv >/dev/null; then
  curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# service user: locked password (key login only), and bash because sshd runs the forced rrsync command via the
# login shell (nologin would refuse it); the authorized_keys line below is what confines the key
id venue-arb >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE" --shell /bin/bash venue-arb
install -d -o venue-arb -g venue-arb "$BASE" "$BASE/bundle" "$BASE/bundle/history" "$BASE/app"

# publisher key: rsync only, confined to the bundle dir, no deletes
install -d -m 700 -o venue-arb -g venue-arb "$BASE/.ssh"
printf '%s\n' "command=\"/usr/bin/rrsync -no-del $BASE/bundle\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding $PUBKEY" \
  >"$BASE/.ssh/authorized_keys"
chown venue-arb:venue-arb "$BASE/.ssh/authorized_keys"
chmod 600 "$BASE/.ssh/authorized_keys"

# app checkout + pipeline-free venv (the repo is not pip-installable; the unit sets PYTHONPATH)
if [[ ! -d $BASE/app/dashboard ]]; then   # skipped when the checkout was copied in by hand
  : "${REPO_URL:?first run needs REPO_URL=<git url>}"
  runuser -u venue-arb -- git clone --branch "$BRANCH" "$REPO_URL" "$BASE/app"
fi
chown -R venue-arb:venue-arb "$BASE/app"
runuser -u venue-arb -- env HOME="$BASE" bash -euo pipefail -c '
  cd /srv/venue-arb/app
  uv python install 3.14
  [ -x .venv/bin/python ] || uv venv --python 3.14 .venv
  uv pip install --python .venv/bin/python fastapi uvicorn duckdb pyarrow pydantic
  PYTHONPATH=. .venv/bin/python -c "import venue_arb.edges, venue_arb.bundle, dashboard.api.app"
'

# units, cron, Caddy
install -m 644 "$BASE/app/deploy/server/venue-arb-web.service" /etc/systemd/system/venue-arb-web.service
install -m 644 "$BASE/app/deploy/server/prune.cron" /etc/cron.d/venue-arb-prune
if [[ -n $DOMAIN ]]; then
  sed "s|^DOMAIN {|$DOMAIN {|" "$BASE/app/deploy/server/Caddyfile" >/etc/caddy/Caddyfile
else
  printf ':443 {\n\ttls internal\n\treverse_proxy 127.0.0.1:8000\n}\n' >/etc/caddy/Caddyfile
fi
systemctl daemon-reload
systemctl enable --now venue-arb-web cron
systemctl enable caddy
systemctl restart caddy

# firewall (the cloud security list must also allow 443)
ufw allow OpenSSH
ufw allow 443/tcp
ufw --force enable

echo "done: curl -s localhost:8000/healthz   (503 'no bundle yet' until the publisher's first push)"
