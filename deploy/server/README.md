# Dashboard server

A small public VM: Caddy (TLS) -> uvicorn `dashboard.api.app` on 127.0.0.1:8000, reading `/srv/venue-arb/bundle`.
The OptiPlex pushes the bundle with rsync over SSH (a key that can only write into that directory, no deletes).
No database or secrets live here; the unit sets only `BUNDLE_DIR` and `PYTHONPATH`.

## Set up (five manual steps)

1. **Create the VM**: Ubuntu 24.04, Oracle Always Free ARM `VM.Standard.A1.Flex` if available, else the AMD
   `E2.1.Micro`. Add your SSH key; note the public IP.
2. **Open 443** in the Oracle cloud security list (VCN > subnet > security list > ingress rule, TCP 443 from
   0.0.0.0/0). `provision.sh` opens it in `ufw` only; the cloud-side list is separate.
3. **Provision**: copy the OptiPlex's publisher public key (`~/.ssh/<key>.pub`, ed25519) and this repo's
   `deploy/server/` to the VM, then
   ```bash
   sudo REPO_URL=<git url the VM can reach> bash provision.sh /path/to/optiplex.pub [domain]
   ```
   The repo is cloned into `/srv/venue-arb/app` (`REPO_BRANCH`, default `main`). If the repo is not reachable from the
   VM, rsync a checkout to `/srv/venue-arb/app` first and omit `REPO_URL`. Without `domain` Caddy serves a self-signed
   cert on `:443` (browsers warn; use the IP); with one, Caddy gets a Let's Encrypt cert (point its DNS A record at the VM first).
   Re-running is safe.
4. **On the OptiPlex**, set `VENUE_ARB_PUBLISH_DEST=venue-arb@<server ip>:/srv/venue-arb/bundle/` in `mise.toml [env]` and
   the three units (`tests/test_deploy.py` needs them identical), and make sure the publisher's ssh uses that key
   (`ssh -i` via `~/.ssh/config` for the host).
5. **Start the publisher**: `cp deploy/*.service ~/.config/systemd/user/ && systemctl --user daemon-reload &&
   systemctl --user restart venue-arb-publish`; backfill with `secrets uv run python -m venue_arb.publish history --hours 168`.

Check: `curl -s localhost:8000/healthz` on the VM (503 `no bundle yet` until the first push, then `{"ok": true, ...}`),
and `curl -k https://<ip>/healthz` from outside.

## Update

```bash
sudo -u venue-arb git -C /srv/venue-arb/app pull
sudo systemctl restart venue-arb-web
```
If dependencies changed, re-run `provision.sh` (idempotent) instead.

## Logs and housekeeping

- App: `journalctl -u venue-arb-web -f`. Caddy: `journalctl -u caddy`, `/var/log/caddy` if logging is enabled.
- `/etc/cron.d/venue-arb-prune` deletes `bundle/history/pairs-*.parquet` older than 9 days (the OptiPlex prunes its own
  copy at 8, so they never fight).
- The venue-arb user has a bash shell only because sshd runs the forced `rrsync` command through it; its key is
  confined by `command="rrsync -no-del ..."`, `no-pty` and the `no-*-forwarding` options.
