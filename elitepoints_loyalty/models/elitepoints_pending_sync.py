from datetime import timedelta

from odoo import fields, models


class ElitePointsPendingSync(models.Model):
    _name = "elitepoints.pending.sync"
    _description = (
        "ElitePoints customer/redemption data for a POS order, staged by "
        "the frontend via RPC before the order itself has reached the "
        "backend. Keyed by the order's client-generated UUID."
    )

    # Why this model exists at all: pos.order._load_pos_data_fields cannot
    # safely be extended in this Odoo build — doing so, even for a single
    # ordinary field, silently breaks the POS frontend's ability to add
    # order lines at all (no crash, no error — a product tap just does
    # nothing). Confirmed not specific to this module, this field, or this
    # nightly; filed upstream as odoo/odoo#291214. See the README for the
    # full isolation writeup.
    #
    # Since the normal field-sync mechanism is off the table, this staging
    # table plus a couple of RPC calls (elitepoints_stage_order_sync on
    # pos.order) does the same job explicitly: the frontend calls the RPC
    # the moment a cashier confirms a customer lookup/redemption, and
    # PosOrder.create()/write() picks the row back up — by order UUID,
    # which is a stock pos.order field synced normally, since it's core's
    # own and not something this module adds — the moment the order itself
    # reaches the backend, then deletes it. Rows only survive if a sale is
    # abandoned after lookup but before payment; the cleanup cron reaps
    # those after a day.
    order_uuid = fields.Char(required=True, index=True)
    customer_ref = fields.Char()
    identifier = fields.Char()
    redeem_amount = fields.Float(default=0.0)

    def _cleanup_stale(self):
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.search([("create_date", "<", cutoff)]).unlink()
