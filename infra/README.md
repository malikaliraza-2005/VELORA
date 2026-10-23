# infra/

How VELORA runs on a server (P12, AD-37). The step-by-step guide is
[`../docs/operations/deployment.md`](../docs/operations/deployment.md).

| Path | What |
|---|---|
| `docker/app.Dockerfile` | The one application image: API and dashboard, worker, monitor, backup job, founder's tools |
| `compose.yaml` | The stack: Caddy (the only published port, 443), API, worker, monitor, PostgreSQL, and the `backup` and `ops` jobs |
| `compose.openwa.yaml` | OpenWA on its own internal network. **Not used** until the Go (V-01) |
| `caddy/Caddyfile` | HTTPS (Let's Encrypt over port 443), HSTS, the reverse proxy |
| `bin/velora` | The founder's tool on the server: build, settings, release, rollback, backup, restore, admin |
| `host/install.sh` | Hardens a fresh Ubuntu 24.04 server and installs the tool and the backup timer |
| `host/systemd/` | The hourly backup timer |
| `tests/stack-drill.sh` | The CI rehearsal of the whole deployment, with the failure drills, a release and a rollback |

Rules:
- Nothing here may contain real credentials. Settings live on the server in `/etc/velora` (owner-only), written by `velora env-init`.
- `backend/tests/deploy/test_compose_policy.py` checks the container rules (published ports, networks, capabilities, pinning) on every change.
