# Testing VELORA on your own computer

Everything built so far (P1 to P9) can be tried by hand on one Windows computer, with
**simulated WhatsApp**. You play both sides: the customer, typing in a terminal window, and
the restaurant, using the staff dashboard in your browser.

> **What this does not test:** real WhatsApp (the OpenWA adapter isn't built until the Go,
> V-01), the AI order reader (the simulator uses the numbered menu), and images such as payment
> screenshots. Nothing here sends a real message to anyone.

## What you need

- **Docker Desktop**, running (check: `docker ps` works).
- **uv** (the Python tool the project already uses).
- **Chrome or Edge.**
- About 20 minutes the first time.

All commands are for **PowerShell**. Paste them one line at a time.

## 1. One-time setup

1. Start a PostgreSQL database just for this. It is separate from the one the automated tests
   use; don't share them, because each one changes the database role passwords.

   ```powershell
   docker run -d --name velora-dev -e POSTGRES_PASSWORD=velora-local-only -p 127.0.0.1:55433:5432 postgres:17
   ```

   The password only works from your own computer. Wait about 10 seconds for it to start.

2. Go to the backend folder and install:

   ```powershell
   cd C:\Users\offic\Projects\VELORA\backend
   uv sync
   ```

3. Create the demo restaurant ("Demo Kitchen"):

   ```powershell
   uv run python -m velora.dev setup --admin-url "postgresql://postgres:velora-local-only@127.0.0.1:55433/postgres"
   ```

   It prints an **owner login link**. Keep it for step 2.3. It works once, for 15 minutes.
   If it expires, run `uv run python -m velora.dev link` for a new one.

   The settings go into `VELORA\.env.dev`. Git ignores this file, and your own `.env` is
   never touched.

## 2. Start it (every time)

You need **two PowerShell windows**, both in `C:\Users\offic\Projects\VELORA\backend`.

1. **Window 1: the server and dashboard.** Leave it running.

   ```powershell
   uv run python -m velora.dev api
   ```

2. **Window 2: the customer's phone.** This window also runs the background worker that
   answers customers and sends status messages, so keep it open while you test.

   ```powershell
   uv run python -m velora.dev customer
   ```

3. **Browser:** open the owner login link. You land on the Orders board, signed in as
   "Demo Owner". The link disappears from the address bar, as intended.

## 3. Get the restaurant ready (in the dashboard)

1. **Menu → "Publish this draft".** The founder's import put the sample menu in the draft;
   customers see nothing until it is published. Check "Live menu, version 1" appears.
2. **Settings:** set **Tax on cash orders = 16** and **Tax on transfer orders = 8**. Put any
   text in **Transfer account details** (e.g. `Meezan Bank, Demo Kitchen, PK00 0000`), then
   **Save settings**. Without a tax rate the bot won't quote that payment method (blocker B3).
3. **Me → Payment PIN:** choose a 6-digit PIN that isn't a simple pattern (e.g. `480135`,
   not `123456` or `111111`). You need it to confirm transfers.
4. The yellow "New orders are stopped" banner should disappear within a few seconds. If it
   says WhatsApp is disconnected, check that window 2 is running.
5. Optional: tap **🔕 Sound off** so it becomes **🔔 Sound on**, to hear new orders.

## 4. Things to test

In window 2, type a message and press **Enter**. The bot's replies appear as `VELORA: …`.

### A. A cash order, start to finish
1. Customer: `hi`, then `MENU`, `2` (Desi), `1` (Biryani), `CHECKOUT`.
2. Customer: `1` (delivery), type an address, `SKIP` (no note), `1` (cash on delivery).
3. Check the summary: Rs 550 + 16% tax (Rs 88) = **Rs 638**. Type `YES`.
4. Dashboard: within about 5 seconds the order appears under **New**, with a
   "New order" banner (and a beep if sound is on).
5. Click **Accept**, **Start preparing**, **Out for delivery**, **Done**. Window 2 shows the
   customer's WhatsApp updates.
6. Customer: `status` shows where the order is.
7. **Double tap:** on a new order, double-click **Accept** quickly. Open **History**: only one
   "Accepted".

### B. A bank transfer
1. Customer: order something, and at payment choose `2` (bank / wallet transfer), then `YES`.
   The confirmation includes your transfer account details.
2. Dashboard: the order shows **Awaiting transfer**. Click **Payment received**.
3. Click **Confirm** with the boxes empty: it asks for the reference, then the PIN, and sends
   nothing.
4. Enter any reference (e.g. `FT 2026 0001`) and a **wrong** PIN: "PIN not accepted".
5. Enter your real PIN: the order becomes **Paid — accept it**. Then **Accept** it.
6. Try the same reference on another transfer order: it is refused as a duplicate.
7. **Payments** tab: your transfers report for today, with a reverse option (owner only).
8. Customer: type `I have paid` on a transfer order. It is shown as evidence in the payment
   panel, but it **doesn't** mark the order paid.

### C. Talking to a person (handoff)
1. Customer: `0`. The bot says staff will reply.
2. Dashboard: **Chats** shows a badge. Open the chat, type a reply and click **Send reply**.
   It appears in window 2.
3. Click **Resume bot**. The customer is back with the assistant, and their cart is kept.

### D. Controls and the menu
1. **Controls → Pause new orders** (15 minutes). A banner appears. Customer: try `CHECKOUT`; the
   bot says ordering is paused. Then **Resume ordering now**.
2. **Menu → Mark sold out** on an item. It disappears from the customer's menu. Then **Back in stock**.
3. **Today:** the day's counts and sales.

### E. Staff and roles
1. **Staff → Invite someone:** name "Kitchen tablet", role **Kitchen tablet (shared)**. Copy
   the link it shows.
2. Open the link in a **private (InPrivate/Incognito) window**, so it is a separate device.
3. The tablet sees only **Orders, Payments, Me**. Go to `http://127.0.0.1:8000/#/settings`:
   it says your role can't open this screen.
4. On the tablet, **Payment received** on a transfer order asks **who is confirming**. Choose
   "Demo Owner" and use your PIN.

### F. Losing the connection
1. In the browser, press **F12 → Network** tab, and change "No throttling" to **Offline**.
2. A red "Connection lost" banner appears. Click **Accept** on an order: it says "this was not
   sent".
3. Set it back to **No throttling**. The banner goes, and the order is unchanged: nothing was
   queued and sent later.

### G. WhatsApp re-link
In this simulation the "phone" is always linked, so the **WhatsApp** tab just shows
"Connected". The disconnect, QR code and re-link flow is covered by the automated browser
tests, and will be tried for real with a test number after the Go.

## 5. Stopping, restarting, starting over

- **Stop:** press **Ctrl+C** in both windows.
- **Next time:** start Docker Desktop, run `docker start velora-dev`, then section 2. Get a fresh
  login link with `uv run python -m velora.dev link`.
- **Start over** with a clean demo restaurant:

  ```powershell
  uv run python -m velora.dev setup --reset --admin-url "postgresql://postgres:velora-local-only@127.0.0.1:55433/postgres"
  ```

- **Remove it all:** `docker rm -f velora-dev`, and delete `VELORA\.env.dev`.

## Customer commands (window 2)

| Type | What it does |
|---|---|
| `hi` | Greeting (the first reply says it's an automated assistant) |
| `MENU` | The menu categories; then reply with numbers |
| `1 x2` | Two of item 1 |
| `CART`, `remove 1`, `CANCEL` | View the cart, remove a line, clear it |
| `CHECKOUT`, then `YES` | Order, then confirm the summary |
| `status` | Where your order is |
| `0` | Talk to a person |
| `STOP` / `START` | Stop messages / start again |
| `/as +923009876543` | Become another customer (a tool command, not sent) |
| `/quit` | Close the customer window (this stops the worker too) |

## Running the automated tests

The automated tests (810+, including the browser tests) run on every pull request in
GitHub. To run them locally, see `backend/README.md`. They use their own database container
(`velora-pg`), not `velora-dev`.
