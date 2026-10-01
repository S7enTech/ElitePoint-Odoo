from odoo import models
from odoo.osv import expression


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _get_available_product_domain(self):
        """Odoo's own domain additionally requires a product to belong to
        one of this shop's configured POS categories (limit_categories).
        The redemption product is synthetic — added programmatically, never
        picked from the product grid — and has no category of its own, so
        it silently failed to load on every shop in this test environment,
        all three of which have category restrictions configured. Confirmed
        live: pos.models["product.product"].get(redeem_id) returned
        undefined on every shop, causing addLineToCurrentOrder to crash
        deep in Odoo core on an undefined product.

        OR it into the domain explicitly rather than touching any
        merchant's own category configuration to work around it.
        """
        domain = super()._get_available_product_domain()
        redeem_product = self.env.ref(
            "elitepoints_loyalty.product_elitepoints_redeem", raise_if_not_found=False
        )
        if redeem_product:
            domain = expression.OR([domain, [("id", "=", redeem_product.id)]])
        return domain

    def _elitepoints_get_credential(self, create_if_missing=False):
        """Finds (or optionally creates) this shop's credential record.

        Lives here rather than as fields on this model itself — see
        elitepoints_pos_credential.py for why pos.config can't safely hold
        secrets.
        """
        self.ensure_one()
        Credential = self.env["elitepoints.pos.credential"].sudo()
        credential = Credential.search([("config_id", "=", self.id)], limit=1)
        if not credential and create_if_missing:
            credential = Credential.create({"config_id": self.id})
        return credential

    def action_open_elitepoints_credential(self):
        """Smart-button action: opens this shop's credential record in its
        own form, creating it on first use."""
        self.ensure_one()
        credential = self._elitepoints_get_credential(create_if_missing=True)
        return {
            "type": "ir.actions.act_window",
            "name": "ElitePoints Credentials",
            "res_model": "elitepoints.pos.credential",
            "view_mode": "form",
            "res_id": credential.id,
            "target": "new",
        }
