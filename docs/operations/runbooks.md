# VELORA runbooks (P11-T6)

- **Status:** written 2026-10-23. **No runbook has been walked through yet.** Each needs one walk-through in staging (P12, [deployment guide](deployment.md)), as a tabletop exercise or a drill, recorded in the table at the end. The pieces that can run without staging (the restore drill and the recovery drill) are automated tests. See [`../planning/implementation-status.md`](../planning/implementation-status.md), "P11 task evidence".
- **Decisions:** AD-19 (one server), AD-21 (backups and restore), AD-22 (stop rules), AD-35 (zero cost), AD-36 (P11 choices) in [`../architecture/architecture-decisions.md`](../architecture/architecture-decisions.md).
- **Who:** "the founder" is whoever is on call. In the pilot, that is the owner.

Every runbook has the same five parts:
1. **Detect:** how you find out.
2. **Act now:** the first minutes.
3. **Manual fallback:** how restaurants keep trading meanwhile.
4. **Recover:** how to get back to normal.
5. **Tell:** who to tell, and what to say.

## The manual fallback (all runbooks)

When VELORA can't take orders, restaurants go back to how they worked before VELORA:
- **Restaurants take orders by phone or on paper.** Their normal phone number keeps working; only the VELORA ordering number is affected.
- **The founder broadcasts nothing automatically.** Never send a mass WhatsApp message to customers about an outage: that is exactly the behaviour that gets numbers banned. Each restaurant tells its own customers in its own way, if it wants to.
- **Orders taken by hand are re-entered in VELORA only if the owner wants it.** By default they stay on paper. Nothing in VELORA depends on them.

To stop new WhatsApp orders everywhere at once (the global pause):

```bash
python -m velora.admin global-pause --reason "<what happened>"
python -m velora.admin global-resume      # when it is fixed
```

To pause one restaurant only, its owner or manager uses **Pause new orders** on the dashboard.

## After any restart: recovery mode

The worker runs **recovery mode** every time it starts (P11-T4). You don't start it by hand. It:
1. checks the database;
2. takes back work a crashed worker was holding;
3. (later, V-04) fetches messages WhatsApp has but VELORA never stored;
4. cancels order summaries from before the outage. Customers who were about to confirm are asked to confirm again;
5. extends unpaid transfer windows by the length of the outage;
6. marks messages that were mid-send as **unknown**. These are never re-sent automatically, because the customer may already have them;
7. replaces old, unsent replies with one "sorry for the delay" message per customer;
8. hands over to the normal sender, which sends what was held, in order, within the rate limit;
9. flags orders placed from messages held during the outage: **"⚠ Placed after a system outage — confirm the order with the customer"** on the order board.

If recovery fails, the worker stops and its supervisor starts it again; recovery then continues where it stopped. A second worker refuses to start while one is running.

**After any outage, staff should:**
- call the customer for every order with the outage flag;
- check **Chats → Messages that need a look** (failed, and "delivery unknown"), and tell each customer by phone if needed.

---

## R1. OpenWA session down, or needs a QR scan

- **Detect:**
  - The alert "channel `needs_qr` / `reconnecting` / `logged_out` for 5 min".
  - The red banner on the dashboard.
  - Customers say the bot isn't answering.
- **Act now:**
  1. VELORA has already stopped taking new orders for that restaurant (the circuit breaker). Replies are held, not lost.
  2. The restaurant owner opens **WhatsApp → Re-link WhatsApp** on the dashboard and scans the QR with the ordering phone.
  3. If the phone is off, out of battery or offline, fix that first. Most disconnects are the phone.
- **Manual fallback:** phone and paper orders for that restaurant.
- **Recover:**
  - When the channel shows **connected**, held messages go out in order.
  - Replies held longer than 30 minutes are replaced by one "sorry for the delay" message.
  - Check **Chats → Messages that need a look**.
- **Tell:** the restaurant owner only. Count the disconnect: V-10 sets the stop-rule threshold (AD-22).

## R2. OpenWA engine broken for all restaurants

- **Detect:**
  - Every channel drops at once, often after an OpenWA or WhatsApp Web update.
  - Several "channel down" alerts within minutes.
- **Act now:**
  1. **Global pause** (above).
  2. **Do not upgrade OpenWA on production numbers to try a fix.** Its version is pinned (P12).
  3. Check the OpenWA project's issue tracker for the same failure.
- **Manual fallback:** phone and paper orders everywhere.
- **Recover:**
  1. Test any new OpenWA version on the **test number first**, never on a restaurant's number.
  2. Once it works there, upgrade, re-link each restaurant (R1), then `global-resume`.
- **Tell:**
  - Every restaurant owner, by phone or individual message: "WhatsApp ordering is paused; please take orders by phone. We will tell you when it is back."
  - No broadcast to customers.

## R3. A number restricted or banned

- **Detect:**
  - The alert "channel `suspected_restricted`" fires at once.
  - WhatsApp tells the restaurant the number is banned or restricted.
- **Act now (stop rule, AD-22):**
  1. **Stop sending.** Global pause if more than one number is affected, otherwise pause that restaurant.
  2. **Stop onboarding** new restaurants.
  3. Do **not** create a replacement number to get around the ban. The owner agreed not to without permission (AD-35, F6).
- **Manual fallback:** phone and paper orders for the affected restaurant.
- **Recover:**
  - The owner decides: appeal through WhatsApp's own process, wait, or move that restaurant to the Meta Cloud API (AD-06, not built yet).
  - Record what happened for the pilot review.
- **Tell:** the restaurant owner, with the facts. Never promise the number will come back.

## R4. Database restore

Use this when the database is lost or damaged, or to run the **restore drill** (required before the pilot, AD-21).

- **Detect:**
  - The alert "monitor can't read the database".
  - `/readyz` failing.
  - The worker refusing to start ("recovery failed").
- **Act now:**
  1. Global pause: if the database still answers, so nothing new is written.
  2. Stop the API and worker containers.
  3. **Don't delete the damaged database:** a restore always goes into a **new** one.
- **Manual fallback:** phone and paper orders everywhere.
- **Recover:**
  1. Fetch the **secret backup key** from where the founder keeps it offline. It is never on the server.
  2. Copy the key to the server for the restore only (e.g. `/root/backup.key`, mode 0600), then:

     ```bash
     sudo velora compose stop api worker monitor
     sudo velora db migrate status                    # still answering? note the version
     sudo velora compose run --rm ops python -m velora.backup list   # newest last
     sudo velora restore --secret-key /root/backup.key --target-db velora_restored
     sudo shred -u /root/backup.key
     ```

     `velora restore` passes the key on standard input: it is never copied into a container. The restore refuses a backup that is incomplete, altered, or encrypted for another key. It then checks the copy:
     - the roles;
     - the schema version;
     - every row count against the backup;
     - FORCE RLS and the policies on every tenant table;
     - the database grants;
     - tenant isolation as the runtime role.

     **Any FAIL line: stop, and don't use the copy.**
  3. Put the copy in place of the damaged database, which is kept aside for later:

     ```bash
     sudo velora compose exec db psql -U postgres        -c 'ALTER DATABASE velora RENAME TO velora_damaged'        -c 'ALTER DATABASE velora_restored RENAME TO velora'
     ```

  4. Set the role passwords again (they aren't in backups) from the server's settings, and migrate if the code is newer than the backup (the restore report says so):

     ```bash
     sudo velora db provision
     sudo velora db migrate up
     ```

  5. Start everything: `sudo velora compose up -d`. Recovery mode treats the time since the backup's last heartbeat as the outage (see "After any restart").
  6. **Place a test order** on a test restaurant, using the test number: the drill's last check.
  7. `sudo velora admin global-resume`.
- **Data loss:**
  - Up to one hour of orders (backups are hourly; RPO target ≤1 h).
  - Orders placed after the newest backup are gone from VELORA. Restaurants have them in WhatsApp and on their paper slips.
  - RTO target: back within 4 hours.
- **Tell:**
  - Every restaurant owner: which time range may be missing, and to re-check orders from it.
  - Record the drill or the incident in the table below.

### Setting up backups (P12, once)

1. On the founder's own computer, **not the server**:

   ```bash
   python -m velora.backup keygen --public-out backup.pub --secret-out backup.key
   ```

   Keep `backup.key` in a password manager, with a printed copy somewhere safe. **Without it no backup can be read.** Losing it means having no backups.
2. On the server, in `/etc/velora/backup.env`, set:
   - `VELORA_BACKUP_PUBLIC_KEY` (the content of `backup.pub`);
   - `VELORA_BACKUP_DATABASE_URL` (the `velora_backup` role);
   - `VELORA_BACKUP_STORAGE` (e.g. `s3://velora-backups/pilot`, with the `VELORA_BACKUP_S3_*` settings);
   - `VELORA_MONITOR_DATABASE_URL`.
3. **The storage key the server holds must not be allowed to delete.** In Backblaze B2, make an application key with read, write and list access only. Then a stolen server can't erase the backups.
4. Add a **lifecycle rule** in the bucket: delete files older than 30 days. That keeps the free 10 GB from filling up.
5. Hourly backups: `sudo systemctl enable --now velora-backup.timer` (P12, [deployment guide](deployment.md)). Each run reports to the monitor.
6. `VELORA_MONITOR_EXPECT_BACKUPS=true` is already set by `velora env-init`: the monitor alerts on a failed backup and on none within 2 hours.
7. Run the restore drill (R4) in staging and record it below.

## R5. Server (VPS) lost

The free VM may be reclaimed or fail (AD-35), and it holds everything (AD-19).

- **Detect:**
  - The external heartbeat service alerts: the monitor's pings stopped.
  - The external uptime check on `/readyz` fails.
  - The dashboard is unreachable.
- **Act now:**
  1. Confirm it's the server, not your own connection: check the Oracle console.
  2. If the VM is gone or won't boot, rebuild. Don't wait for it.
- **Manual fallback:** phone and paper orders everywhere.
- **Recover:**
  1. Create a new VM and rebuild it with the [deployment guide](deployment.md), "From scratch", steps 1–7. You need the settings again: if `/etc/velora` is lost with the server, `env-init` makes new ones (new passwords), and you fill in the alert, Groq and backup values again.
  2. **Restore the newest backup (R4)** in place of the new, empty database.
  3. Recovery mode runs on the worker's start.
  4. **Every restaurant re-links WhatsApp (R1).** OpenWA's session files are deliberately not backed up (P11-T2, PC-8): they are WhatsApp logins in plaintext.
  5. Issue new OpenWA API keys ([validation plan §10.3](../planning/openwa-validation-plan.md)).
  6. Point the hostname's DNS at the new server. Certificates are issued again automatically.
- **Tell:** every restaurant owner, with a time estimate, and again when each one has re-linked.

## R6. Payment dispute or staff mistake

- **Detect:**
  - A customer says they paid, but the order isn't marked paid.
  - Or it was marked paid, but no money arrived.
  - The owner's daily transfer report doesn't match the bank statement.
- **Act now:**
  1. Remember: a screenshot or "I have paid" never marks an order paid (P8). Only a staff member with a PIN does, against a reference.
  2. The owner opens **Payments → Transfers report** and compares it with the bank app.
- **Manual fallback:** none needed. Ordering keeps working.
- **Recover:**
  - **Marked paid by mistake:** the owner uses **Reverse payment** on that payment (owner PIN and a reason). An order not yet accepted goes back to "awaiting transfer". The reference is freed.
  - **Paid but expired:** use **Payments → Late payments**: accept, or refund (refund pending).
  - **A wrong price, or an order falsely marked paid, is a stop rule (AD-22).** Pause, find the cause, and record it.
- **Tell:**
  - The customer, by the restaurant, from its own phone.
  - Record any stop-rule event for the pilot review.

---

## Walk-through record

Each runbook is walked through once in staging (P11-T6 acceptance). Add a row for each walk-through.

| Date | Runbook | Kind (tabletop / drill) | Who | Result and fixes |
|---|---|---|---|---|
| — | R4 restore | Automated drill in CI (`tests/backup/test_backup_restore.py::test_restore_drill`) | CI | Passes. A test order succeeds on the restored copy. **The staging drill is still to do.** |
| — | Recovery mode | Automated drill in CI (`tests/orders/test_recovery_mode.py::test_recovery_drill_after_a_one_hour_outage`) | CI | Passes. **The staging drill is still to do.** |
| — | R4 restore, through the deployed stack | Automated rehearsal in CI on every push (`infra/tests/stack-drill.sh`, P12) | CI | `velora backup`, then `velora restore` into a new database: all checks OK. **Staging still to do.** |
| — | Worker crash | Automated rehearsal in CI (`infra/tests/stack-drill.sh`) | CI | The worker process killed: Docker restarts it and recovery mode runs again. **Staging still to do.** |
