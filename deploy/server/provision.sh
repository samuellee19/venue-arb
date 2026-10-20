#!/usr/bin/env bash
# Provision the venue-arb dashboard server (fresh Ubuntu 24.04, run as root; safe to re-run).
#   sudo REPO_URL=<git url> bash provision.sh /path/to/optiplex.pub <public-ip-or-domain>
# REPO_URL is needed only on the first run (when /srv/venue-arb/app has no checkout; a hand-copied one is fine); REPO_BRANCH defaults to main.
set -euo pipefail

PUBKEY_FILE=${1:?usage: provision.sh /path/to/optiplex.pub <public-ip-or-domain>}
SITE=${2:?usage: provision.sh /path/to/optiplex.pub <public-ip-or-domain>}
BRANCH=${REPO_BRANCH:-main}
BASE=/srv/venue-arb
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -r $PUBKEY_FILE ]] || { echo "cannot read $PUBKEY_FILE" >&2; exit 1; }
PUBKEY=$(cat "$PUBKEY_FILE")
[[ $PUBKEY != *$'\n'* ]] || { echo "key file must be one line" >&2; exit 1; }
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

# push user: locked password (key login only), and bash because sshd runs the forced rrsync command via the
# login shell (nologin would refuse it); the root-owned authorized_keys line below is what confines the key
id venue-arb >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /bin/bash venue-arb
# web user: runs uvicorn, no shell, no home; reads the root-owned app and the 0755/0644 bundle, writes nothing
id venue-arb-web >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin venue-arb-web
install -d -m 755 -o root -g root "$BASE" "$BASE/app"
install -d -m 755 -o venue-arb -g venue-arb "$BASE/bundle" "$BASE/bundle/history"
# everything under $BASE but the bundle is root's (a re-run converts a checkout, venv or uv dirs the push user owned)
find "$BASE" -mindepth 1 -maxdepth 1 ! -name bundle -exec chown -R root:root {} +

# publisher key: rsync only, confined to the bundle dir, no deletes; root-owned so the push user cannot rewrite it
install -d -m 700 -o root -g root "$BASE/.ssh"
printf '%s\n' "restrict,command=\"/usr/bin/rrsync -munge -no-del $BASE/bundle\" $PUBKEY" >"$BASE/.ssh/authorized_keys"
chown root:root "$BASE/.ssh/authorized_keys"
chmod 600 "$BASE/.ssh/authorized_keys"

# app checkout + pipeline-free venv, as root (the repo is not pip-installable; the unit sets PYTHONPATH)
if [[ ! -d $BASE/app/dashboard ]]; then   # skipped when the checkout was copied in by hand
  : "${REPO_URL:?first run needs REPO_URL=<git url>}"
  (cd / && git clone --branch "$BRANCH" "$REPO_URL" "$BASE/app")
fi
(cd / && env HOME="$BASE" bash -euo pipefail -c '
  cd /srv/venue-arb/app
  uv python install 3.14
  [ -x .venv/bin/python ] || uv venv --python 3.14 .venv
  uv pip install --exclude-newer 2026-10-05T00:00:00Z --python .venv/bin/python fastapi uvicorn duckdb pydantic
  PYTHONPATH=. .venv/bin/python -c "import venue_arb.edges, venue_arb.bundle, dashboard.api.app"
')

# units, cron, Caddy
install -m 644 "$BASE/app/deploy/server/venue-arb-web.service" /etc/systemd/system/venue-arb-web.service
install -m 644 "$BASE/app/deploy/server/prune.cron" /etc/cron.d/venue-arb-prune
# a bare IP cannot get a Let's Encrypt cert, so it gets Caddy's internal CA (browser warning; curl -k)
{
  echo "$SITE {"
  [[ $SITE =~ ^[0-9.]+$ ]] && printf '\ttls internal\n'
  printf '\treverse_proxy 127.0.0.1:8000\n}\n'
} >/etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now cron
systemctl enable venue-arb-web
systemctl restart venue-arb-web   # a re-run applies new code, deps and unit changes
systemctl enable caddy
systemctl restart caddy

# firewall (the cloud security list must also allow 443)
RULES=/etc/iptables/rules.v4
if [[ -f $RULES ]]; then
  # Oracle images: a static INPUT REJECT precedes anything ufw appends (and Oracle advises against ufw), so open 443 there
  if ! grep -q -- '--dport 443 ' "$RULES"; then
    awk '!d && /^-A INPUT .*-j REJECT/ {print "-A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT"; d=1} {print}' \
      "$RULES" >"$RULES.new" && mv "$RULES.new" "$RULES"
    grep -q -- '--dport 443 ' "$RULES" || { echo "no INPUT REJECT line in $RULES; open 443 by hand" >&2; exit 1; }
  fi
  iptables-restore <"$RULES"
else
  ufw allow OpenSSH
  ufw allow 443/tcp
  ufw --force enable
fi

echo "done: curl -s localhost:8000/healthz   (503 'no bundle yet' until the publisher's first push)"
