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

Built against the documented Odoo 18 API surface (config-parameter-backed
settings, `pos.order` create/write hooks, `_load_pos_data_fields`, the
`ControlButtons` OWL patch point, the `dialog` service). **Not yet run
against a live Odoo 18 instance** — there was none available in the
environment this was built in. Before submitting to the App Store:

1. Spin up an Odoo 18 dev instance (Odoo.sh free trial, or
   `docker run odoo:18`) and install this module from a local addons path.
2. Configure a test store's API key/secret against
   `elitepoint-backend-staging.up.railway.app` and walk through: settings
   test-connection, POS customer lookup, a points-only sale, a sale with a
   partial redemption, and a forced sync failure (kill network mid-sale) to
   confirm the retry cron recovers it.
3. Replace the placeholder-free `static/description/index.html` images
   (`icon.png`, `banner.png` — not yet created, see below) with real
   ElitePoints branding.
4. Create an Odoo Apps publisher account at odoo.com/apps and submit
   through their review flow.

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
