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
- **Full points-only and partial-redemption sales, against real
  ElitePoints staging credentials, start to finish** — add product, look
  up a real customer, (optionally redeem part of their balance), pay,
  validate — with the resulting orders confirmed `synced` and real
  points earned/redeemed in the database afterward, including one real
  sync failure caught and confirmed recovered by retry. See "Confirmed
  working end-to-end" below for detail, and the sections before it for
  what it took to get there — four bugs total, two in Odoo core and two
  in this module's own code, none of them previously exercised until
  this pass. (This describes the initial App Store submission; see
  "v2: per-shop credentials for multi-store merchants" below for a real
  architectural fix made after it went live, and "v2.0.1" for a real
  security bug that fix introduced and fixed within the same day.)

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

### A fourth bug — the redemption line was invalidating its own sync

Confirmed with a real points balance: a $140 points-only sale earns
points, so the same test customer had exactly 1.4 points on the books by
the time a redemption was tried. Looking them up, applying a $1 partial
redemption, paying, and validating all worked correctly on the frontend —
cart total, receipt, everything reflected the discount exactly right. But
the order's `elitepoints_sync_status` came back `failed`:

```
ElitePoints error: ['items.1.unitPrice must not be less than 0', 'items.1.totalPrice must not be less than 0']
```

`_elitepoints_build_items()` was including the synthetic "ElitePoints
Redemption" line itself in the `items` array sent to `redeem_points` — a
real purchased-item list should never contain a negative-priced entry for
the discount mechanism itself, and the backend correctly rejects it. Fixed
by excluding any line whose product is the redemption product from the
built items list; the backend already derives the discount from
`gross_amount`/`redeem_amount` separately, so the line was never needed
there anyway.

Retried the same failed order by hand afterward (the exact call the
15-minute retry cron makes) and it went from `failed` → `synced`,
`elitepoints_points_redeemed: 1`, no error — which is also, in effect, the
"forced sync failure, confirm the retry cron recovers it" test: a real
failure (not a contrived one) followed by a real successful retry.

### Confirmed working end-to-end against real ElitePoints staging credentials

With all four fixes in place, both scenarios verified live against real
ElitePoints staging credentials, database-checked afterward each time:

- **Points-only sale**: add product, look up a real customer, pay,
  validate. `elitepoints_sync_status: synced`, `elitepoints_points_earned:
  1` — a real point grant, not a mock.
- **Partial redemption**: look up a customer with a real balance, redeem
  part of it, pay, validate. Cart/receipt/total all correctly reflected
  the discount. First attempt caught the items bug above (a real, useful
  failure); after the fix, `elitepoints_sync_status: synced`,
  `elitepoints_points_redeemed: 1`, `elitepoints_redeem_amount` and
  `elitepoints_customer_ref` both correctly carried over from the staged
  RPC data.

Before submitting to the App Store:

1. ~~Get past the core crash and confirm the POS button/dialog~~ — done.
2. ~~Create an Odoo Apps publisher account~~ — done.
3. ~~Points-only sale, partial redemption, and a failed-sync retry~~ —
   all done, confirmed live against real staging credentials (see above).
4. Submit through the Odoo Apps review flow, category **Point of Sale**
   (there's no "Loyalty" category — Odoo categorizes by which app a module
   extends, and comparable connector/integration apps all live under Point
   of Sale, matching the `category` already set in the manifest).

## v2 (18.0.2.0.0): per-shop credentials for multi-store merchants

After the App Store listing went live, a real architectural gap surfaced:
ElitePoints already models one API key/secret pair as one store's identity
— that's how `/odoo/auth` has always worked. v1 of this module stored that
single key/secret **company-wide** (one `ir.config_parameter`, set once on
the shared Point of Sale Settings page). That's fine for a merchant with
one location, but wrong for a merchant running several physical stores off
one shared Odoo database — a completely normal Odoo setup, since one
database sharing inventory/accounting across multiple `pos.config` "shops"
is the standard multi-location pattern, not an edge case. Every shop in
that database would have been forced to authenticate as the exact same
ElitePoints store, merging all of their sales under one store ID server
-side — reporting like "best store" would have nothing to rank, because
ElitePoints would only ever see one undifferentiated store where there
were actually several.

**Fixed by moving credentials from company-wide Settings to each shop's
own record.** `elitepoints_api_key`/`elitepoints_api_secret`/
`elitepoints_base_url` now live on `pos.config` itself (Point of Sale >
Configuration > Point of Sale > a shop > ElitePoints Loyalty), alongside a
per-shop cached access token. Every register running under one shop
naturally shares that shop's identity; a different shop in the same
database gets its own. `elitepoints_client.py`'s entire public surface
(`lookup_customer`, `get_customer_balance`, `earn_points`,
`redeem_points`, `test_connection`) now takes the `pos.config` it's acting
on, and the frontend passes `this.pos.config.id` through on every call
that needs it. Confirmed this doesn't affect what a customer can do:
ElitePoints balances are partner-wide, not store-scoped, so a customer can
still earn at one shop and redeem at another — this change only fixes
*attribution* of which store a sale happened at, not what the customer
sees.

The old company-wide Settings page and its fields are gone outright, not
deprecated in place — keeping both would mean two sources of truth for
the same thing. Since this was a real schema and UI relocation on a
module that was already live, a migration
(`migrations/18.0.2.0.0/post-migrate.py`) carries the one old shared
credential forward onto *every* existing shop on upgrade, so nothing
silently stops authenticating — every shop keeps working exactly as
before (all sharing the one key they already had) until an admin gives
any of them their own distinct key.

**Verified against the real staging backend, not assumed:**

- Upgraded a real multi-shop install (3 active shops sharing one
  credential pre-upgrade) and confirmed the migration copied that
  credential onto all three.
- Proved shops are actually isolated, not just configured separately: set
  one shop to the real credential, a second to a deliberately wrong one,
  and a third to blank. `is_configured` correctly reported
  true/true/false; `test_connection` and a real `lookup_customer` call
  succeeded on the real-credential shop and failed cleanly on the wrong
  one — critically, the wrong shop never succeeded by way of the correct
  shop's cached token, which is the specific cross-store bug this
  architecture could have silently introduced if a token were cached
  once per database instead of once per shop.
- Ran a complete real sale through the actual POS UI on the
  real-credential shop end to end — ElitePoints button, customer lookup,
  payment, validation — and confirmed the order synced
  (`elitepoints_sync_status: synced`) with real points earned against the
  real staging backend, with the correct shop recorded on the order.

## v2.0.1 (18.0.2.0.1): a real security bug in v2, and a second real bug it was hiding behind

Running the full test suite above against the actual POS UI — not just
the backend-level isolation tests — surfaced a genuine security bug in
v2's own design, caught before it had any real-world exposure.

**The credentials themselves were leaking to the cashier's browser.**
`pos.config._load_pos_data_fields` returns `[]`, and in Odoo's own
convention for that method, an empty list means "every field", not
"none" — core's own default behavior for a model's own current record,
not a bug in core. Storing `elitepoints_api_key`/`api_secret`/
`access_token` as plain fields directly on `pos.config` put them inside
that "every field" set: confirmed live, via
`window.posmodel.config.elitepoints_api_secret`, that a shop's live API
secret and access token were both sitting in plain sight in the POS
frontend's own JS state — readable by any cashier via browser devtools.

Fixed by moving credentials off `pos.config` entirely into a new
`elitepoints.pos.credential` model — never part of Point of Sale's own
synced-model list, so nothing in it reaches the frontend at all,
regardless of what `_load_pos_data_fields` does. `pos.config` now only
knows how to find (or create) its own credential record; the "Test
Connection" UI moved from an inline field block to a smart button that
opens the credential as its own small popup form. A migration
(`migrations/18.0.2.0.1`) carries every shop's existing credentials
into the new table and then **drops the old `pos_config` columns
outright** — leaving them in place unreferenced would have meant the
already-leaked secrets just kept sitting there with no access control
at all.

**Fixing it immediately surfaced a second, unrelated real bug, hidden
behind the first.** With the leak fixed, a real redemption attempt
through the POS UI crashed: `TypeError: Cannot read properties of
undefined (reading 'taxes_id')`, deep inside Odoo core's
`addLineToOrder`. Traced to `this.pos.models["product.product"].get()`
returning `undefined` for the redemption product — the same failure
mode as the original `sale_ok` bug, but a different cause this time:
`pos.config._get_available_product_domain()` also requires a product to
belong to one of the shop's configured POS categories
(`limit_categories`/`iface_available_categ_ids`) when that restriction
is enabled, and the synthetic redemption product has no category of its
own. All three demo shops in this test environment have category
restrictions configured — a realistic setup, not a staging-only
quirk — so this would have broken redemption for any real merchant
using category-limited POS screens. Fixed by overriding
`_get_available_product_domain()` to `OR` the redemption product into
the domain explicitly, rather than touching any merchant's own category
configuration to route around it.

Both confirmed fixed against the real staging backend: a full
points-only sale and a full partial-redemption sale, run end to end
through the actual POS UI from a genuinely fresh session (new tab,
cleared asset cache, closed prior session — ruling out any stale-cache
explanation for the earlier crash), both completing with no frontend
error and `elitepoints_sync_status: synced` on the resulting order. Also
re-confirmed the frontend no longer exposes any `elitepoints_*` key on
`pos.config` at all after the fix.

## A fifth bug — a redemption never earned points on what the customer still paid

Asked directly: "a customer redeems part of their balance and pays the
rest — do they also earn points on the remaining amount?" The honest
answer at the time was that this had never actually been verified —
every redemption test run against staging so far used amounts small
enough (under $1 remaining) that 1% of the remainder rounds to $0.00,
which would hide this exact gap either way.

Re-ran it with a cart large enough to tell the difference either way
(a $115 sale, redeeming a customer's full $2.19 balance, leaving
$112.81 actually paid). The real balance moved from 2.19 to **exactly**
1.13 — `2.19 − 2.19 + 1.13`, 1% of $112.81 rounded to two places — so
the backend was correctly computing and applying the earn all along.
But Odoo's own order record showed `elitepoints_points_earned: 0`
regardless, on every redemption, every time.

Traced to `ElitePoint-Backend`: `recordRedemption` computes `pointsEarned`
on `amount - redeemAmount` and applies it to the customer's real balance
correctly, but its HTTP response only ever returned
`{status, pointsRedeemed}` — the earned figure never left the backend.
Odoo's own sync code only read `pointsRedeemed` on that branch too, so
even a fixed backend response would have gone unread. Both sides needed
fixing:

- **Backend** ([S7enTech/ElitePoint-Backend#202](https://github.com/S7enTech/ElitePoint-Backend/pull/202)):
  added `pointsEarned` to `RedeemPointsResult` and all three return paths
  in `recordRedemption` — the normal success path, and both replay paths
  for a duplicate/concurrent post (which now replay the originally-earned
  figure instead of silently dropping it on retry). Covered by a new test
  asserting the returned value; the existing 12 tests in that file all
  still pass unchanged.
- **This module**: `_sync_elitepoints`'s redeem branch now also reads
  `result.get("pointsEarned", 0)` into `order.elitepoints_points_earned`,
  the same way the earn branch always has.

Deployed the backend fix to the shared ElitePoints staging environment
(not just the disposable Odoo-only test project) and re-ran the exact
same $115/$2.19 scenario end to end through the real POS UI: Odoo's own
order record now shows a nonzero earned figure matching the real
balance change, where before it always showed 0.

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
- ~~Multi-store-per-Odoo-database support~~ — resolved in v2 (see above):
  each shop now has its own ElitePoints credentials.
