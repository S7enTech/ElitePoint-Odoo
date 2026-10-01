from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

DEFAULT_BASE_URL = "https://api.myelitepoints.com/v1/api"


class ElitePointsPosCredential(models.Model):
    """One shop's ElitePoints credentials, deliberately kept off pos.config.

    pos.config's own data is synced wholesale to the POS frontend —
    `_load_pos_data_fields` returning `[]` means "read every field", not
    "read none", which is Odoo's own convention for that method, not a bug
    in core. Storing secrets directly as pos.config fields put them there
    too: confirmed live that a shop's API key, API secret, and access token
    were all readable from the cashier's browser console. This model is
    never part of point_of_sale's synced model list, so nothing here ever
    reaches the frontend — see the README for the full incident writeup.
    """

    _name = "elitepoints.pos.credential"
    _description = "ElitePoints Credentials (per shop, server-side only)"

    config_id = fields.Many2one(
        "pos.config", required=True, ondelete="cascade", index=True
    )
    api_key = fields.Char(string="ElitePoints API Key")
    api_secret = fields.Char(string="ElitePoints API Secret")
    base_url = fields.Char(string="ElitePoints API URL", default=DEFAULT_BASE_URL)
    access_token = fields.Char(readonly=True, copy=False)
    token_expires_at = fields.Float(readonly=True, copy=False)

    _sql_constraints = [
        (
            "config_unique",
            "unique(config_id)",
            "This shop already has ElitePoints credentials configured.",
        ),
    ]

    def action_elitepoints_test_connection(self):
        """Tests the credentials currently entered in the form, even if the
        record has not been saved yet."""
        self.ensure_one()
        if not self.api_key or not self.api_secret:
            raise UserError(
                _("Enter both an API key and an API secret before testing.")
            )

        self.write(
            {
                "base_url": self.base_url or DEFAULT_BASE_URL,
                "access_token": False,
                "token_expires_at": 0.0,
            }
        )

        self.env["elitepoints.client"].test_connection(self.config_id)

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
