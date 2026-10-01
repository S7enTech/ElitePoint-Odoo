from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

DEFAULT_BASE_URL = "https://api.myelitepoints.com/v1/api"


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Credentials live per-shop, not company-wide: a partner's API key is
    # what identifies a specific store to ElitePoints. A merchant running
    # several locations off one shared Odoo database needs each shop
    # authenticating as its own store, not all of them sharing one
    # identity — see the README for the full reasoning. copy=False on the
    # credential fields so duplicating a shop in Odoo never silently hands
    # the copy the original's store identity.
    elitepoints_api_key = fields.Char(
        string="ElitePoints API Key",
        copy=False,
        help="This store's API key issued by your ElitePoints admin.",
    )
    elitepoints_api_secret = fields.Char(
        string="ElitePoints API Secret",
        copy=False,
        help="This store's API secret issued by your ElitePoints admin. "
        "Keep this private.",
    )
    elitepoints_base_url = fields.Char(
        string="ElitePoints API URL",
        default=DEFAULT_BASE_URL,
        copy=False,
    )
    elitepoints_access_token = fields.Char(readonly=True, copy=False)
    elitepoints_token_expires_at = fields.Float(readonly=True, copy=False)

    def action_elitepoints_test_connection(self):
        """Tests the credentials currently entered in the form, even if the
        record has not been saved yet."""
        self.ensure_one()
        if not self.elitepoints_api_key or not self.elitepoints_api_secret:
            raise UserError(
                _("Enter both an API key and an API secret before testing.")
            )

        # Persist first so the client reads the same values visible on
        # screen, then clear any stale cached token from a previous
        # connection before testing.
        self.write(
            {
                "elitepoints_base_url": self.elitepoints_base_url
                or DEFAULT_BASE_URL,
                "elitepoints_access_token": False,
                "elitepoints_token_expires_at": 0.0,
            }
        )

        self.env["elitepoints.client"].test_connection(self)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("ElitePoints"),
                "message": _("Connected successfully."),
                "type": "success",
                "sticky": False,
            },
        }
