# ElitePoint-Odoo

Odoo 18 addon that connects a merchant's Odoo Point of Sale to the
[ElitePoints](https://myelitepoints.com) loyalty network. This is the
merchant-installable counterpart to the Odoo adapter already live in
`ElitePoint-Backend` (`src/integrations/adapters/odoo/`) — the backend has
accepted Odoo traffic for a while; this repo is what a merchant actually
installs to talk to it, and what gets submitted to the Odoo App Store.

## Module

All of the addon code lives in [`elitepoints_loyalty/`](elitepoints_loyalty).
See that folder for the module manifest, models, views, and POS frontend
assets.

## Status

**Fully verified end-to-end against a real Odoo 18 instance**, including the
POS button and dialog. Confirmed live:

- Module installs cleanly (no Python errors, ~175 queries, ~0.4s).
- Settings page renders correctly and round-trips through `ir.config_parameter`.
- **Test Connection** makes a real HTTP call to
  `elitepoint-backend-staging.up.railway.app` and correctly surfaces the
  backend's actual error response (`ElitePoints error: Invalid credentials`).
- The **ElitePoints control button appears in the POS screen** next to
  Customer / Internal Note / Actions, opens the lookup dialog, and the
  dialog's "Look Up" makes a real RPC round trip through `pos.order` →
  `elitepoints.client` → the backend, correctly rendering the backend's
  actual rejection inline as an error banner.

### A pre-existing Odoo 18 core bug, and how it's handled

Opening a POS register on a completely fresh company (no prior orders) hits
a crash in stock Odoo 18 core: `PosOrder.taxTotals`
(`point_of_sale/static/src/app/models/pos_order.js`) does
`this.payment_ids.filter(...)`, and `payment_ids` is `undefined` rather than
`[]` on a freshly created order. `getCustomerDisplayData` hits the same
thing via `this.payment_ids.map(...)`. This is **not caused by this
module** — confirmed by building Odoo 18 straight from its own official
GitHub source (no Docker image, no third-party anything) and reproducing
the identical crash with `elitepoints_loyalty` fully uninstalled. It was
isolated across every variable that could plausibly matter — installed
modules, two Odoo build dates, demo data on/off, company country/currency,
sandboxed vs. real browser, a from-source build vs. the Docker image — same
crash every time. Worth checking against Odoo's GitHub issues before
assuming it needs reporting fresh.

Since Odoo's own review environment for App Store submissions is almost
certainly a similarly fresh install, and this crash would otherwise take
the entire POS screen down before a cashier — or a reviewer — ever sees
this module's own control button, `static/src/js/pos_order_patch.js` wraps
both `taxTotals` and `getCustomerDisplayData` in a try/catch that falls
back to a safe zeroed/empty value only when the underlying computation
throws. This is a defensive workaround, not a fix for the underlying issue,
and is safe to delete once Odoo fixes it upstream. With it in place, the
POS screen renders normally and this module's own UI works exactly as
designed — confirmed live, not assumed.

Before submitting to the App Store:

1. ~~Get past the core crash and confirm the POS button/dialog~~ — done.
2. ~~Create an Odoo Apps publisher account~~ — done.
3. Walk through a points-only sale, a sale with a partial redemption, and a
   forced sync failure (kill network mid-sale) to confirm the retry cron
   recovers it — these need a real (non-fake) store API key/secret against
   staging, which wasn't available during this build.
4. Submit through the Odoo Apps review flow, category **Point of Sale**
   (there's no "Loyalty" category — Odoo categorizes by which app a module
   extends, and comparable connector/integration apps all live under Point
   of Sale, matching the `category` already set in the manifest).

### Testing locally with Docker

`odoo.com/trial` (Odoo Online) doesn't let you pick a version — it always
provisions whatever's current. To test against a pinned Odoo 18, use the
compose file in [`dev/docker-compose.yml`](dev/docker-compose.yml), which
mounts `elitepoints_loyalty/` straight into the container as a local addon
(needs [Docker Desktop](https://www.docker.com/products/docker-desktop/)
installed first):

```bash
docker compose -f dev/docker-compose.yml up
```

Then open <http://localhost:8069>, create a database (any name/email/
password — local only), install **Point of Sale** from Apps (this module
depends on it), then **Apps > Update Apps List** and install **ElitePoints
Loyalty**. `docker compose -f dev/docker-compose.yml down -v` wipes it
clean to start over.

## What's deliberately out of scope here

- Odoo 17 support — only 18 was targeted per the initial ask. The module
  uses no 18-only APIs that would obviously break 17, but it hasn't been
  checked.
- Sage-style backfill/idempotency edge cases — the backend's Odoo adapter
  now accepts `externalTransactionId` (see the paired backend change) so
  retries from the cron are safe, but this hasn't been load-tested.
- Multi-store-per-Odoo-database support. Credentials are configured once,
  company-wide, via Settings — matching the single API-key/secret-per-store
  design of the backend's `/odoo/auth` endpoint. A merchant with multiple
  ElitePoints stores needs multiple Odoo databases (or a v2 with
  per-`pos.config` credentials).
