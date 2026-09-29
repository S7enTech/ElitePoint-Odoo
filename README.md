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

Tested end-to-end against a real Odoo 18 instance (a throwaway Railway
deployment — see `railway.json`/`Dockerfile`, not for production use).

**Verified working:**
- Module installs cleanly (no Python errors, 191 queries, ~0.4s).
- Settings page renders correctly and round-trips through `ir.config_parameter`.
- **Test Connection** makes a real HTTP call to
  `elitepoint-backend-staging.up.railway.app` and correctly surfaces the
  backend's actual error response (`ElitePoints error: Invalid credentials`)
  — proves the client, auth flow, and error handling all work against the
  real backend contract.

**Blocked, not by this module:** opening a POS register on that same test
instance hits a pre-existing crash in stock Odoo 18 core
(`point_of_sale/static/src/app/models/pos_order.js` — `taxTotals`, called
from `ProductScreen`'s header-total render and from
`Chrome.sendOrderToCustomerDisplay`). This was isolated rigorously, not
assumed:
- Reproduces identically with `elitepoints_loyalty` fully uninstalled.
- Reproduces identically with `pos_online_payment` (a stock POS dependency,
  unrelated to this module) also uninstalled.
- Unminified stack traces (`?debug=assets`) show every frame inside
  `point_of_sale` core or `owl.js` — never a file from this module.
- Disabling Customer Display (`pos_config.customer_display_type = 'none'`)
  removes the `sendOrderToCustomerDisplay` trigger but the `ProductScreen`
  render path still crashes the same way.

So the actual control button / dialog code (`static/src/js/control_buttons.js`,
`elitepoints_dialog.js`) has **not been visually confirmed in a browser** —
the crash happens before `ControlButtons` renders. Whoever picks this up
next should reproduce on a real desktop Chrome outside a sandboxed/headless
environment first (this was hit in an automated browser pane whose Web
Workers may be restricted — `bus/websocket_worker_bundle` was failing and
retrying continuously in the same session, which is a plausible contributing
factor). If the crash doesn't reproduce there, the button code was probably
fine all along and just needs a normal click-through. If it does reproduce
on a real browser, it's an Odoo 18 core / `point_of_sale` bug worth checking
against Odoo's own tracker before assuming it's fixable here.

Before submitting to the App Store:

1. Get past the above on a real browser and walk through: POS customer
   lookup, a points-only sale, a sale with a partial redemption, and a
   forced sync failure (kill network mid-sale) to confirm the retry cron
   recovers it.
2. ~~Create an Odoo Apps publisher account~~ — done.
3. Submit through the Odoo Apps review flow, category **Point of Sale**
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
