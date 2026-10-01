"""Carries the old company-wide ElitePoints credential forward onto every
existing shop.

Before this version, elitepoints.api_key/api_secret/base_url were a single
ir.config_parameter shared by the whole database. From this version on,
each pos.config (shop) has its own. Without this step, upgrading would
silently blank out every shop's credentials — the integration would look
unconfigured and simply stop working until someone noticed and re-entered
them. Instead, every existing shop keeps working exactly as before (all
sharing the one key they already had), and an admin can then give any shop
its own distinct key at their own pace.

Defensive column check: an install that jumps straight from 18.0.1.0.0 to
a later version in one upgrade (skipping ever being on this exact version)
runs this script against a schema already updated to the *final* target
version's model code — which, as of 18.0.2.0.1, no longer has these
columns on pos_config at all (see that version's migration). Without the
check this would crash with "column does not exist" and block the whole
upgrade; with it, this step is simply skipped as not applicable, and
18.0.2.0.1's own migration handles credentials from whatever schema
actually exists.
"""


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _column_exists(cr, "pos_config", "elitepoints_api_key"):
        return

    cr.execute(
        "SELECT key, value FROM ir_config_parameter "
        "WHERE key IN ('elitepoints.api_key', 'elitepoints.api_secret', "
        "'elitepoints.base_url')"
    )
    values = dict(cr.fetchall())
    api_key = values.get("elitepoints.api_key")
    api_secret = values.get("elitepoints.api_secret")
    base_url = values.get("elitepoints.base_url")

    if not (api_key and api_secret):
        return

    if base_url:
        cr.execute(
            "UPDATE pos_config SET elitepoints_api_key = %s, "
            "elitepoints_api_secret = %s, elitepoints_base_url = %s",
            (api_key, api_secret, base_url),
        )
    else:
        cr.execute(
            "UPDATE pos_config SET elitepoints_api_key = %s, "
            "elitepoints_api_secret = %s",
            (api_key, api_secret),
        )
