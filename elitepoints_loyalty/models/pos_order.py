import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

SYNC_STATES = [
    ("not_applicable", "No ElitePoints customer on this order"),
    ("pending", "Pending"),
    ("synced", "Synced"),
    ("failed", "Failed"),
]

# Orders reach a terminal paid state through more than one code path
# (frontend sync via create_from_ui, refunds, manual backend edits), so the
# sync is triggered from create()/write() rather than from a single private
# method that may not exist, or fire, on every path.
SYNCABLE_STATES = ("paid", "done", "invoiced")

MAX_SYNC_ATTEMPTS = 8


class PosOrder(models.Model):
    _inherit = "pos.order"

    elitepoints_customer_ref = fields.Char(
        string="ElitePoints Customer ID",
        help="Customer ID in ElitePoints, set when a cashier looks up a "
        "customer at this order.",
        copy=False,
    )
    elitepoints_identifier = fields.Char(
        string="ElitePoints Lookup Identifier",
        help="The phone number, email, or barcode used to look up the "
        "customer for this order.",
        copy=False,
    )
    elitepoints_redeem_amount = fields.Monetary(
        string="ElitePoints Redeemed (Currency)",
        default=0.0,
        copy=False,
        help="Amount deducted from the customer's ElitePoints balance "
        "against this order.",
    )
    elitepoints_points_earned = fields.Integer(
        string="ElitePoints Earned", readonly=True, copy=False
    )
    elitepoints_points_redeemed = fields.Integer(
        string="ElitePoints Redeemed", readonly=True, copy=False
    )
    elitepoints_sync_status = fields.Selection(
        SYNC_STATES,
        string="ElitePoints Sync Status",
        default="not_applicable",
        readonly=True,
        copy=False,
    )
    elitepoints_sync_error = fields.Text(readonly=True, copy=False)
    elitepoints_sync_attempts = fields.Integer(
        default=0, readonly=True, copy=False
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        # Odoo's POS frontend only reads/writes fields that are on this
        # allowlist — a field left off it is invisible to the frontend even
        # though it exists on the model, so setting it in JS silently never
        # reaches the server. This is the one line that makes the fields
        # above actually round-trip.
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list += [
            "elitepoints_customer_ref",
            "elitepoints_identifier",
            "elitepoints_redeem_amount",
            "elitepoints_points_earned",
            "elitepoints_points_redeemed",
            "elitepoints_sync_status",
        ]
        return fields_list

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders:
            if (
                order.elitepoints_customer_ref
                and order.state in SYNCABLE_STATES
                and order.elitepoints_sync_status in ("not_applicable", False)
            ):
                order.elitepoints_sync_status = "pending"
                order._sync_elitepoints()
        return orders

    def write(self, vals):
        result = super().write(vals)
        if vals.get("state") in SYNCABLE_STATES:
            for order in self:
                if (
                    order.elitepoints_customer_ref
                    and order.elitepoints_sync_status in ("not_applicable", "pending", "failed", False)
                ):
                    order._sync_elitepoints()
        return result

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def _elitepoints_build_items(self):
        self.ensure_one()
        items = []
        for line in self.lines:
            items.append(
                {
                    "productId": str(line.product_id.id),
                    "name": line.product_id.display_name,
                    "quantity": line.qty,
                    "unitPrice": line.price_unit,
                    "totalPrice": line.price_subtotal_incl,
                }
            )
        return items

    def _elitepoints_payment_method_label(self):
        self.ensure_one()
        methods = self.payment_ids.mapped("payment_method_id.name")
        return ", ".join(methods) if methods else None

    def _sync_elitepoints(self):
        """Posts this order to ElitePoints as an earn or redeem.

        Never raises: a network hiccup or a misconfigured store must not
        block a cashier from finishing a sale that has already been paid
        for. Failures are recorded on the order and retried by
        ir.cron `elitepoints_retry_failed_syncs`.
        """
        client = self.env["elitepoints.client"]
        for order in self:
            if order.elitepoints_sync_status == "synced":
                continue
            if not order.elitepoints_customer_ref:
                order.elitepoints_sync_status = "not_applicable"
                continue
            if order.elitepoints_sync_attempts >= MAX_SYNC_ATTEMPTS:
                continue

            order.elitepoints_sync_attempts += 1
            external_transaction_id = order.pos_reference or f"pos-order-{order.id}"
            items = order._elitepoints_build_items()
            payment_method = order._elitepoints_payment_method_label()

            try:
                if order.elitepoints_redeem_amount > 0:
                    gross_amount = order.amount_total + order.elitepoints_redeem_amount
                    result = client.redeem_points(
                        customer_ref=order.elitepoints_customer_ref,
                        amount=gross_amount,
                        redeem_amount=order.elitepoints_redeem_amount,
                        description=f"POS Sale {order.pos_reference or order.name}",
                        payment_method=payment_method,
                        items=items,
                        external_transaction_id=external_transaction_id,
                    )
                    order.elitepoints_points_redeemed = result.get(
                        "pointsRedeemed", 0
                    )
                else:
                    result = client.earn_points(
                        customer_ref=order.elitepoints_customer_ref,
                        amount=order.amount_total,
                        description=f"POS Sale {order.pos_reference or order.name}",
                        transaction_date=fields.Datetime.to_string(
                            order.date_order
                        ),
                        payment_method=payment_method,
                        items=items,
                        external_transaction_id=external_transaction_id,
                    )
                    order.elitepoints_points_earned = result.get(
                        "pointsEarned", 0
                    )

                order.elitepoints_sync_status = "synced"
                order.elitepoints_sync_error = False
            except Exception as exc:  # noqa: BLE001 - must never propagate
                _logger.error(
                    "ElitePoints sync failed for POS order %s (attempt %s): %s",
                    order.id,
                    order.elitepoints_sync_attempts,
                    exc,
                )
                order.elitepoints_sync_status = "failed"
                order.elitepoints_sync_error = str(exc)

    @api.model
    def _elitepoints_retry_failed_syncs(self):
        failed_orders = self.search(
            [
                ("elitepoints_sync_status", "=", "failed"),
                ("elitepoints_sync_attempts", "<", MAX_SYNC_ATTEMPTS),
            ],
            limit=100,
        )
        if failed_orders:
            _logger.info(
                "Retrying ElitePoints sync for %s order(s)", len(failed_orders)
            )
            failed_orders._sync_elitepoints()

    # ------------------------------------------------------------------
    # RPC surface for the POS frontend
    # ------------------------------------------------------------------

    @api.model
    def elitepoints_lookup_customer(
        self, identifier, id_type, first_name=None, last_name=None
    ):
        return self.env["elitepoints.client"].lookup_customer(
            identifier, id_type, first_name=first_name, last_name=last_name
        )

    @api.model
    def elitepoints_get_balance(self, customer_ref):
        return self.env["elitepoints.client"].get_customer_balance(customer_ref)

    @api.model
    def elitepoints_get_redeem_product_id(self):
        product = self.env.ref(
            "elitepoints_loyalty.product_elitepoints_redeem", raise_if_not_found=False
        )
        return product.id if product else False

    @api.model
    def elitepoints_is_configured(self):
        return self.env["elitepoints.client"].is_configured()
