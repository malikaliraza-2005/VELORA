# VELORA deployment guide (P12)

- **Status:** written 2026-10-23. **No server exists yet.** Everything here runs in CI as a rehearsal on every push (the "Deployment rehearsal" job, `infra/tests/stack-drill.sh`). The first real run, timed, on a free VM is P12-T6, and the staging drills are P12-T7. Choices are in AD-37.
- **Cost:** $0 a month (AD-35). Nothing here needs a paid service.
- **Related:** [`runbooks.md`](runbooks.md) (what to do when something breaks), [`../../infra/`](../../infra/) (the files this guide uses).

## What runs where

One server (AD-19), Ubuntu 24.04, with Docker. Only port 443 is open to the internet; SSH is open to your own addresses only.

| Container | What it is | Reaches |
|---|---|---|
| `caddy` | HTTPS for the dashboard and API, with a free automatic certificate | the internet (443 in); `api` |
| `api` | `python -m velora.serve`: API and staff dashboard | `db` |
| `worker` | `python -m velora.workers`: messages, orders, timers; recovery mode on every start | `db`; Groq |
| `monitor` | `python -m velora.monitoring run`: alerts, daily report, heartbeat | `db`, `api`; your alert email or webhook |
| `db` | PostgreSQL 17, on the server's own disk | nothing |
| `backup` (hourly job) | `python -m velora.backup run`, encrypted to your key | `db`; the backup bucket |
| `ops` (when you run it) | your tools: provisioning, migrations, onboarding, restores | `db`; the backup bucket |
| `openwa` | **not running**: waits for the Go (V-01) | api and worker only |

On the server:

| Path | What |
|---|---|
| `/opt/velora` | the repository, checked out at the running release |
| `/etc/velora/*.env` | the settings, one file per container, owner-only (root, 0600). **Never copy them anywhere else.** |
| `/etc/velora/release.env` | the running and previous release (image tags and commits) |
| `/etc/velora/releases.log` | every release and rollback, with times |
| `/usr/local/bin/velora` | the tool (`infra/bin/velora`) |
| Docker volumes `velora_pgdata`, `velora_caddy_data` | the database; the certificates |

## Before you start (once)

| You need | Free option | Status |
|---|---|---|
| A VM: 2+ CPUs, 4+ GB memory, Ubuntu 24.04 | Oracle Cloud Always Free (Ampere A1, arm64; the image is built for it) | **B5, V-03:** terms not verified |
| A hostname pointing at the VM | a free subdomain service, or a domain you own | **B12:** not chosen |
| An alert email (SMTP) or a webhook to your phone | a free mailbox with SMTP; a free push service | **B11:** not set up |
| A heartbeat (dead man's switch) and an uptime check on `/readyz` | free tiers of cron-job or uptime monitors | **B11** |
| A bucket for backups (S3-compatible) | Backblaze B2 or Cloudflare R2 free tier | **B5** |
| The backup key pair | `python -m velora.backup keygen` on **your own computer** (runbook R4, "Setting up backups") | — |
| Your SSH key, and your computer's public address | — | — |

In the cloud console:
- Allow TCP **443 from anywhere** and TCP **22 from your address only**. Nothing else, not even 80.
- Add your SSH public key to the VM.

## From scratch (P12-T6: one hour or less)

Start a timer. Write down the time each numbered step finishes; the table at the end of this section is the evidence.

1. **Create the VM** (Ubuntu 24.04, your SSH key, the firewall rules above). Point the hostname's DNS at its public address.
2. **Log in and get the code:**

   ```bash
   ssh ubuntu@<server address>
   sudo git clone https://github.com/malikaliraza-2005/VELORA.git /opt/velora
   ```

3. **Harden the server** (SSH keys only, firewall, automatic security updates, Docker, swap, the `velora` tool and the backup timer):

   ```bash
   sudo /opt/velora/infra/host/install.sh --admin-user ubuntu --ssh-from <your address>/32
   ```

   It refuses to run if your own SSH session would be locked out. Log in again in a **second** terminal before closing the first, to prove you still can.
4. **Build the image** (about 5 minutes on a free VM):

   ```bash
   sudo velora build
   ```

5. **Write the settings** (fresh random passwords for every database role):

   ```bash
   sudo velora env-init --env staging --domain <your hostname> --alert-email <you@example.com>
   sudo nano /etc/velora/monitor.env   # SMTP server, sender, login; the heartbeat URL
   sudo nano /etc/velora/backup.env    # the PUBLIC backup key; the bucket and its key
   sudo nano /etc/velora/worker.env    # the Groq key (optional: without it, numbered menu only)
   sudo nano /etc/velora/ops.env       # VELORA_OPERATOR: your name, for the audit log
   sudo velora env-check               # repeat until it says "settings ready"
   ```

   Use `--env production` for the production server. Staging accepts only test restaurants (slugs starting `test-`) and test numbers.
6. **First release:**

   ```bash
   sudo velora release HEAD --first
   ```

   It checks the settings, creates the roles and the database, applies the migrations, starts every container, and waits until `https://<hostname>/readyz` answers with a valid certificate and HSTS.
7. **Switch on hourly backups** and check them:

   ```bash
   sudo systemctl enable --now velora-backup.timer
   sudo velora backup
   sudo velora monitor test-alert      # the alert reaches your email or phone
   ```

8. **Check from outside**, on your own computer (in `backend/`):

   ```bash
   uv run python -m velora.deploy scan <server address> --expect 443 --expect 22
   ```

   It must say PASS: only 443 and 22 open (22 only because you scan from your own address; from anywhere else, only 443). Then open `https://<hostname>` in a browser: the padlock shows a valid certificate.
9. **Onboard the test restaurant** (runbook steps in `backend/README.md`, "Founder onboarding tools"):

   ```bash
   sudo velora admin tenant-create --slug test-grill --name "Test Grill" --prefix TG
   ```

Stop the timer.

| Date | Step 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | Total | Who | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| — | | | | | | | | | | | | **Not run yet: no VM (B5, V-03).** The CI rehearsal does steps 4–7 from nothing in under a minute (41 s, CI run 39940522360). |

## Releasing a new version

**Release checklist** (every release):

1. CI is green on `main` for the commit you release (all three checks, including the deployment rehearsal).
2. **Migrations are backward compatible:** the previous release's code must still work on the new schema (add columns and tables; never drop or rename in the same release that stops using them). Otherwise a rollback can't work, and the only way back is a restore (R4).
3. No restaurant is mid-rush: release before 11:00 or after 23:00 PKT.
4. Run, on the server:

   ```bash
   cd /opt/velora && sudo git fetch origin
   sudo velora release origin/main
   ```

   It builds the image from exactly that commit, checks the settings, **backs up first** (and stops if the backup fails), migrates, switches every container, and waits until the dashboard answers. Recovery mode runs on the worker's start, so customers with an open order summary are asked to confirm again (AD-36).
5. Open the dashboard and check one order on the test restaurant.
6. Watch `sudo velora status` and your alerts for 15 minutes.

**Rollback** (the new version misbehaves):

```bash
sudo velora rollback
```

It switches every container back to the previous release's image and files (they are kept on the server) and waits until the dashboard answers. Migrations are **never** reverted (fix forward); see checklist item 2. Running `velora rollback` again returns to the newer release.

Old images stay on the server. Remove ones older than the previous release now and then: `sudo docker image ls velora-app`, then `sudo docker image rm velora-app:<tag>`.

**OpenWA versions** are pinned in `/etc/velora/stack.env` (`VELORA_OPENWA_IMAGE`, exact version) once the Go is given, and only changed in staging first (runbook R2).

## Staging

- A separate stack with `--env staging`, its own hostname and its own settings: **test restaurants and test numbers only**. The `velora admin` tool refuses a restaurant slug without `test-` in staging.
- **Never restore a production backup into staging.** Staging's restore drill uses staging's own backups.
- Zero cost: if the free tier allows only one usable VM, staging runs there first. Once every drill below has passed, it is rebuilt as production: new settings (`env-init`), new volumes, a new hostname if needed (AD-37 asks the owner to confirm this).

## Rotating a password

Change the password inside the matching URL in **every** file that has it (`env-check` lists any mismatch), then:

```bash
sudo velora env-check
sudo velora db provision          # sets every role's password from the settings
sudo velora compose up -d --force-recreate
```

The PostgreSQL administrator's password (`db.env` and `VELORA_ADMIN_DATABASE_URL`) is set on the first start only. To change it, use `sudo velora compose exec db psql -U postgres -c '\password postgres'`, then update both files.

## Failure drills (P12-T7)

Each drill passes only if the alert reaches the founder. Run each in staging and add a row.

| Drill | How | Expected alert | CI rehearsal (every push) | Staging |
|---|---|---|---|---|
| Kill OpenWA | `sudo velora compose kill openwa` | channel down | **Gated** (the Go, V-01) | Not run |
| Force a disconnect | unlink the test number on the phone | channel needs a QR scan (R1) | **Gated** (the Go, V-01) | Not run |
| Take the API down | `sudo velora compose stop api` | `api_down`, then cleared after `start api` | Passes: alert received and cleared (HTTPS webhook stand-in) | Not run |
| Restore the database | runbook R4 | none (checks in the restore report) | Passes: backup, restore into a new database, all checks OK | Not run |
| Worker crash | kill the worker process | none: Docker restarts it, recovery mode runs | Passes | Not run |
| Release and rollback | `velora release`, `velora rollback` | — | Passes: release, rollback, roll forward | Not run |

Each CI run's results are in that run's summary (the drill record).
