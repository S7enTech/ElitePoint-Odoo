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
- **A full points-only sale, against real ElitePoints staging credentials,
  start to finish** — add product, look up a real customer, pay, validate
  — with the resulting order confirmed `synced` and a real point grant in
  the database afterward. See "Confirmed working end-to-end" below for
  detail, and the two sections before it for what it took to get there.

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

### A second, more severe core Odoo bug — worked around

While running the real-credentials test below, hit a second core defect,
worse than the first: extending `pos.order._load_pos_data_fields()` with
**any** custom field — the standard, documented mechanism a module would
normally use to get the ElitePoints customer reference from the POS
frontend to the backend — silently breaks the ability to add products to
an order at all. No crash, no console error; a product tap is just
swallowed, cart stays at 0 items forever.

Isolated with the same rigor as the first bug: an A/B test on the same
shop/session with only the module's install state changed (installed →
broken, uninstalled → works), then bisected file-by-file down to
`_load_pos_data_fields` specifically, then binary-searched the exposed
field list down to a single, ordinary `Float` field still reproducing it.
Then checked whether it was specific to the pinned nightly — it isn't:
reproduces identically on `18.0-20260119`, `18.0-20260803` (the pin), and
`18.0-20260926` (the latest available at the time), a ~9 month span, so
this isn't a transient regression window to build around.

Filed upstream as
[odoo/odoo#291214](https://github.com/odoo/odoo/issues/291214), with the
minimal repro and the cross-build results. No existing report or fix found
for this exact interaction despite a fairly thorough search; possibly
related to [#196157](https://github.com/odoo/odoo/issues/196157) (a
different but adjacent `taxTotals`/`payment_ids` crash, also open, also
unrelated to any custom module) and
[#213425](https://github.com/odoo/odoo/issues/213425) ("endless loading of
the cash register" with loyalty installed — different proximate cause, but
establishes 18.0's POS + custom-`pos.order`-extension interaction has had
more than one nightly-specific reactivity regression).

**Worked around** rather than waited on: `pos_order.py` no longer overrides
`_load_pos_data_fields` at all. Instead, `elitepoints_customer_ref`,
`elitepoints_identifier`, and `elitepoints_redeem_amount` travel from
frontend to backend over RPC — the same pattern the customer lookup/balance
calls already used successfully (`elitepoints_lookup_customer`,
`elitepoints_get_balance`). `control_buttons.js` calls
`elitepoints_stage_order_sync` the moment a cashier confirms a lookup or
redemption, staging the values in a new side model
(`elitepoints.pending.sync`) keyed by the order's client-generated `uuid`
— a stock `pos.order` field that syncs normally, since it's core's own and
not something this module adds. `PosOrder.create()`/`write()` pick the
staged row back up by UUID the moment the order itself reaches the
backend, copy it onto the real fields, and delete it; a daily cron reaps
rows left behind by an abandoned sale (customer looked up, never paid).

While fixing this, also found (via live introspection, not assumption)
that the redemption-line code was calling `order.add_product(...)`, which
doesn't exist on this Odoo build's `PosOrder` — the current API is
`PosStore.addLineToCurrentOrder(...)`. Would have thrown the moment a
cashier confirmed any redemption amount > 0; fixed alongside the RPC
rework since it's the same code path and was about to be exercised for the
first time by the real-credentials test.

### A third bug, entirely this module's own — the dialog result was always discarded

Wiring the RPC rework above exposed a real, pre-existing bug that had
nothing to do with either Odoo core issue: `clickElitePoints()` opened
`ElitePointsDialog` with `this.dialog.add(ElitePointsDialog, { getPayload,
close: (result) => this._onElitePointsDialogClosed(order, result) })`.
Confirmed via a live-instrumented prototype patch that `confirm()` inside
the dialog ran fine, but `_onElitePointsDialogClosed` on `ControlButtons`
was never called — zero times, in any test.

The cause, straight from Odoo's own `dialog_service.js`:

```js
subProps: markRaw({ ...props, close }),
```

`close` is a name the dialog service always injects itself, spread in
*after* the caller's own props — so any `close` a caller passes is
silently discarded and replaced with the service's own no-argument dismiss
function. `ElitePointsDialog.confirm()` calling `this.props.close({...})`
was therefore always just closing the dialog and throwing the result away;
it never had any effect beyond dismissal, since the module was first
written. Renamed the callback to `onConfirm` (a name the service doesn't
reserve) and had `confirm()`/`cancel()` call the real `props.close()`
(no arguments) afterward to actually dismiss. Confirmed fixed the same way
the bug was found — an instrumented live prototype patch showing the
callback firing — before removing the instrumentation.

### Confirmed working end-to-end against real ElitePoints staging credentials

With all three fixes in place: added a product, looked up a real
ElitePoints test customer by phone (a real RPC round trip returning their
real balance), applied the lookup, paid, and validated. Checked the
resulting `pos.order` directly in the database afterward —
`elitepoints_customer_ref` and `elitepoints_identifier` correctly carried
over from the staged RPC data, `elitepoints_sync_status` was `synced`
(not `failed`), and `elitepoints_points_earned` was `1` — a real point
grant from the real ElitePoints staging backend, not a mock.

Before submitting to the App Store:

1. ~~Get past the core crash and confirm the POS button/dialog~~ — done.
2. ~~Create an Odoo Apps publisher account~~ — done.
3. ~~Points-only sale~~ — done, confirmed live against real staging
   credentials (see above). Still to walk through: a sale with a partial
   redemption (needs a test customer with a real points balance — the
   customer used above now has exactly 1 point from that first sale, not
   enough for a meaningful redemption yet), and a forced sync failure
   (kill network mid-sale) to confirm the retry cron recovers it.
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
