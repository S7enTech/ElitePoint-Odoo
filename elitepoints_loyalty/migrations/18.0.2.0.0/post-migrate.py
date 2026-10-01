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
"""


def migrate(cr, version):
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
