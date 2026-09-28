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

1. **Test locally with Docker** (see below) and walk through: settings
   test-connection, POS customer lookup, a points-only sale, a sale with a
   partial redemption, and a forced sync failure (kill network mid-sale) to
   confirm the retry cron recovers it. Point the store credentials at
   `elitepoint-backend-staging.up.railway.app` so nothing touches
   production data.
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
