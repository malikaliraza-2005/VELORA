# Owner verification and account recovery (AD-12)

- **Status:** written 2026-10-24 (AD-38), for the pilot (P13-T1 precondition). Not yet used with a real restaurant.
- **Why:** scanning the WhatsApp QR code proves who holds a phone, not who owns the business. Before a restaurant goes live, the founder checks the person, the phone and the business, in person where possible. VELORA refuses `go-live` until a verification and a consent are recorded (P3).
- **Related:** [`consent-form.md`](consent-form.md) (signed at the same visit), [`runbooks.md`](runbooks.md) R3 (a banned number).

## What to check

Check all three. Write down what you saw and where you keep the evidence. **Never put a CNIC image, or any document, into VELORA, a chat or the repository.**

| Check | How (free) | Enough when |
|---|---|---|
| **1. The person** | See the owner's **original CNIC** in person. Compare the photo and name. Note the last 4 digits only. | The name matches the person in front of you, and they say they own or run the restaurant |
| **2. The business** | At least two of these:<br>- **visit the restaurant** while it's open;<br>- an **NTN** in FBR's online taxpayer lookup;<br>- a **food authority licence** (e.g. Punjab Food Authority, Sindh Food Authority) on the wall or in its online lookup;<br>- the restaurant's **Google Maps or Foodpanda listing** with the same name and address;<br>- a **utility bill or rent agreement** for the premises in the owner's or the business's name. | Two independent sources name the same business at the same address, and connect it to the owner |
| **3. The phones** | **Main number:** call the restaurant's main number from your own phone; the owner answers or confirms. **Ordering number:** the owner shows the new SIM in the phone that will run WhatsApp Business, and you send it a test message that they read back to you. | You reached both numbers yourself, and the ordering number is **not** the main number |

**Red flags (stop and don't go live):**
- the person can't show an original CNIC, or it doesn't match;
- you can't find the restaurant at the address, or the name differs everywhere;
- they want to link the restaurant's **main** WhatsApp number;
- they push to skip the consent form or Part A (the ban risk);
- someone other than the owner asks to be the owner on the dashboard.

## Recording it

Keep the evidence (your notes, photos of the shop front, the signed consent) in a private folder, e.g. your password manager or a private drive folder named after the restaurant. Then record where it is, not the evidence itself:

```bash
velora admin verification-record --tenant <slug> --method in_person --status verified \
  --evidence-ref "drive: VELORA/verification/<slug>/2026-10-24"
```

| Method | Use for |
|---|---|
| `in_person` | you met the owner and visited (preferred) |
| `cnic` | the CNIC check was the main evidence |
| `ntn` | an FBR NTN lookup was the main evidence |
| `business_document` | a licence, bill or agreement |
| `other` | anything else; say what in the evidence reference |

- **Not sure yet:** use `--status pending`. **Failed:** use `--status rejected`, and don't go live.
- **Corrections:** a record can't be edited. Record a new one with `--supersedes <id>`.

Then record the three consents ([`consent-form.md`](consent-form.md)), invite the owner (`velora admin owner-invite`), and only then run `velora admin go-live`.

## Account recovery

Every recovery is manual and done by the founder, after checking it's really the owner (the same checks as above: in person, or a call to the restaurant's **main** number). Never act on a message from the ordering number alone: whoever holds that phone isn't necessarily the owner.

| Situation | What to do |
|---|---|
| **The owner lost their phone, or the login link** | Verify them (in person, or a call to the main number). Then `velora admin tenants` to find their staff id, and `velora admin recovery-link --tenant <slug> --staff-id <id>`: a single-use link for 15 minutes. Give it to them privately, never in a group chat. On the dashboard they can log out all other devices. |
| **A staff member left** | The owner removes them on the dashboard (Staff). That ends all their sessions at once. If the owner can't, verify the owner and help them do it with a recovery link. |
| **The ordering phone was lost or stolen** | Global pause for that restaurant (`velora admin global-pause` only if every restaurant is affected; otherwise pause ordering on its dashboard). The owner unlinks VELORA from WhatsApp on another device, or recovers the number with the operator. Re-link with a new QR scan (runbook R1). |
| **The ordering number was banned** | Runbook R3. Don't create a new account to get around the ban (Part A of the consent). |
| **The owner changes (the restaurant was sold)** | Treat it as a new restaurant: verify the new owner, sign a new consent, and record both with `--supersedes`. The old owner is removed from the dashboard. |
| **Someone claims to be the owner and isn't on record** | Don't act. Call the restaurant's main number and the owner on record. |

Record each recovery in your own notes with the date, who asked, how you verified them and what you did. Dashboard actions are in VELORA's audit log already.
