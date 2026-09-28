from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    elitepoints_api_key = fields.Char(
        string="ElitePoints API Key",
        config_parameter="elitepoints.api_key",
        help="Store API key issued by your ElitePoints admin.",
    )
    elitepoints_api_secret = fields.Char(
        string="ElitePoints API Secret",
        config_parameter="elitepoints.api_secret",
        help="Store API secret issued by your ElitePoints admin. Keep this private.",
    )
    elitepoints_base_url = fields.Char(
        string="ElitePoints API URL",
        config_parameter="elitepoints.base_url",
        default="https://api.myelitepoints.com/v1/api",
    )

    def action_elitepoints_test_connection(self):
        """Tests the credentials currently entered in the form, even if the
        settings page has not been saved yet."""
        self.ensure_one()
        if not self.elitepoints_api_key or not self.elitepoints_api_secret:
            raise UserError(
                _("Enter both an API key and an API secret before testing.")
            )

        # Persist first so the client reads the same values the cashier is
        # looking at, then clear any stale cached token from a previous
        # connection before testing.
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elitepoints.api_key", self.elitepoints_api_key)
        params.set_param("elitepoints.api_secret", self.elitepoints_api_secret)
        params.set_param(
            "elitepoints.base_url",
            self.elitepoints_base_url or "https://api.myelitepoints.com/v1/api",
        )
        params.set_param("elitepoints.access_token", "")
        params.set_param("elitepoints.token_expires_at", "")

        self.env["elitepoints.client"].test_connection()

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
