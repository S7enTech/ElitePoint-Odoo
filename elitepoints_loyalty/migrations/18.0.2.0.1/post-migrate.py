"""Moves ElitePoints credentials off pos.config and into their own table,
then drops the old columns.

v18.0.2.0.0 stored credentials as plain fields on pos.config. That was a
real security bug, not just an architecture smell: pos.config's own data
is synced wholesale to the POS frontend (its _load_pos_data_fields
returning [] means "every field", in Odoo's own convention, not "none"),
so every shop's live API key, API secret, and access token were readable
from the cashier's browser console. Confirmed live before this fix.

This migration carries each shop's existing credentials forward into the
new elitepoints_pos_credential table (which is never part of POS's synced
model list, so nothing in it reaches the frontend), then drops the old
pos_config columns outright — leaving them in place as unreferenced,
access-control-free columns would mean the leaked secrets just kept
sitting there.
"""


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    # Nothing to carry forward if the old columns were never created in
    # the first place — e.g. an install that jumped straight to this
    # version without ever running 18.0.2.0.0's migration (see that
    # script's docstring for why this can happen on a direct upgrade).
    if not _column_exists(cr, "pos_config", "elitepoints_api_key"):
        return

    cr.execute(
        "SELECT id, elitepoints_api_key, elitepoints_api_secret, "
        "elitepoints_base_url, elitepoints_access_token, "
        "elitepoints_token_expires_at "
        "FROM pos_config "
        "WHERE elitepoints_api_key IS NOT NULL "
        "OR elitepoints_api_secret IS NOT NULL"
    )
    rows = cr.fetchall()

    for (
        config_id,
        api_key,
        api_secret,
        base_url,
        access_token,
        token_expires_at,
    ) in rows:
        cr.execute(
            "INSERT INTO elitepoints_pos_credential "
            "(config_id, api_key, api_secret, base_url, access_token, "
            "token_expires_at, create_date, write_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, now(), now())",
            (
                config_id,
                api_key,
                api_secret,
                base_url,
                access_token,
                token_expires_at,
            ),
        )

    for column in (
        "elitepoints_api_key",
        "elitepoints_api_secret",
        "elitepoints_base_url",
        "elitepoints_access_token",
        "elitepoints_token_expires_at",
    ):
        cr.execute(f"ALTER TABLE pos_config DROP COLUMN IF EXISTS {column}")
