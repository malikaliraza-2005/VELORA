# OpenWA trial kit (P0-E1)

Throwaway test tooling for the live OpenWA trial (the **reduced variant**, AD-39): one test SIM, your own phone as the customer, your Windows computer. **It never serves a real restaurant**, and nothing here is product code. The test plan is [`../docs/planning/openwa-validation-plan.md`](../docs/planning/openwa-validation-plan.md). Its appendix is the step-by-step guide for the phone; this page is the commands.

**Before you start:**
- The SIM is on WhatsApp Business (appendix, Part 1).
- Docker Desktop is running (appendix, Part 2).
- **Safety rules (appendix, Part 6):**
  - message only your own numbers, or people who agreed;
  - at any WhatsApp warning, **stop** and screenshot it;
  - never share the QR code, `.env`, or OpenWA's logs.

## 1. Set up (once)

Open this `trial` folder, right-click an empty area, choose **Open in Terminal**, and run:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
docker compose up -d
docker compose ps        # openwa and openwa-db "healthy", recorder "Up"
```

`setup.ps1` writes `.env` with fresh random secrets, and copies the evidence log template to `evidence\evidence-log.csv`. **`.env` is secret.**

## 2. Link the test number

```powershell
docker compose run --rm tool link biz1
```

Then on this computer:
1. Open **http://localhost:2785**.
2. Sign in with the `API_MASTER_KEY` value from `.env`.
3. Open the session `biz1`, and scan its QR code with **WhatsApp Business** on the test SIM: ⋮ → Linked devices → Link a device.

**Record T3.9:** did a passkey prompt appear, and did linking work?

```powershell
docker compose run --rm tool status     # biz1 should be "ready"
```

## 3. During a test session

Claude gives you a short script for each test, from `evidence\evidence-log.csv`. These are the commands it uses:

| What | Command |
|---|---|
| Record resource use (T5.1), in a second window | `powershell -ExecutionPolicy Bypass -File sample-resources.ps1` |
| Make the recorder fail with 500s (T1.1) | `docker compose run --rm tool mode error` |
| Make the recorder hang (T1.2) | `docker compose run --rm tool mode hang` |
| Recorder back to normal | `docker compose run --rm tool mode ok` |
| Recorder fully down, then back (T1.3, T2.x) | `docker compose stop recorder` … `docker compose start recorder` |
| Restart OpenWA (T2.2, T3.2) | `docker compose restart openwa` |
| Save OpenWA's stored messages from the last hour (T2.x) | `docker compose run --rm tool messages biz1 --minutes 60` |
| Save failed webhook deliveries (T1.x) | `docker compose run --rm tool failures biz1` |
| Send one message to **your own** number (T4.2) | `docker compose run --rm tool send biz1 923001234567 "test"` |
| Send twice with one key: must arrive once (T4.3) | `docker compose run --rm tool send biz1 923001234567 "once" --key t43-1` (run it twice) |
| A copy of a file with phone numbers masked, for sharing | `docker compose run --rm tool mask /evidence/<file>` |

The recorder writes every webhook to `evidence\webhooks-<date>.jsonl`: arrival time, headers, raw body, whether the signature verified, and what it answered.

**Don't share raw evidence.** Share only the `-masked` copies, the evidence log and screenshots.

## 4. Pausing and finishing

- **Pause:** `docker compose stop`. Resume with `docker compose start`; the session should come back without a new QR code (T3.2).
- **Finish:**
  1. `docker compose run --rm tool logout biz1`;
  2. on the phone, check that **Linked devices** no longer shows it;
  3. `docker compose down -v`.

  `down -v` **deletes** the WhatsApp login and OpenWA's message store. Keep the `evidence` folder.

## What is set, and why

- **OpenWA v0.25.0**, pinned by digest: the version the desk review read (§10).
- **Baileys** engine first. Set `ENGINE_TYPE=whatsapp-web.js` in `.env` to test the other one, then re-link.
- **Desk-review baseline (§10.6):**
  - 5 webhook attempts;
  - failed payloads kept 72 hours, so they can be redriven;
  - the admin key and the API-key pepper set before first boot;
  - a reconnect limit of 10;
  - messages kept 30 days (PC-7);
  - no simulated typing (no detection-evasion features, AD-24).
- **Only on this computer:** OpenWA listens on `127.0.0.1` only, and its database is on an internal network. The recorder is reachable only by OpenWA.
- **Known (seen while building the kit, 2026-10-24):** OpenWA writes the admin key into its log at first boot, even when the key is given in `.env`. **Treat `docker compose logs openwa` as secret**, and never paste it anywhere. (T3.7 records it properly.)
