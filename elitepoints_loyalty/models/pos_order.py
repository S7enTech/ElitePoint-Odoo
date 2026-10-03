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
    elitepoints_redeem_amount = fields.Float(
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

    # Deliberately NOT overriding _load_pos_data_fields here. Doing so —
    # even for a single ordinary field — silently breaks the POS frontend's
    # ability to add order lines at all in this Odoo build: no crash, no
    # error, a product tap just does nothing. Confirmed not specific to
    # this module, this field, or this nightly (reproduces across every
    # 18.0 build tried, Jan–Sep 2026); filed upstream as
    # odoo/odoo#291214. See the README for the full isolation writeup.
    #
    # Instead, elitepoints_customer_ref/identifier/redeem_amount travel
    # from frontend to backend via elitepoints_stage_order_sync (an RPC
    # call, same pattern as elitepoints_lookup_customer below) staged in
    # elitepoints.pending.sync, keyed by the order's UUID — a stock
    # pos.order field that syncs normally since it's core's own, not
    # something this module adds. _elitepoints_apply_pending_sync picks the
    # row back up the moment the order itself reaches the backend.

    def _elitepoints_apply_pending_sync(self):
        pending_model = self.env["elitepoints.pending.sync"].sudo()
        for order in self:
            if order.elitepoints_customer_ref or not order.uuid:
                continue
            pending = pending_model.search(
                [("order_uuid", "=", order.uuid)], limit=1
            )
            if not pending:
                continue
            order.write(
                {
                    "elitepoints_customer_ref": pending.customer_ref,
                    "elitepoints_identifier": pending.identifier,
                    "elitepoints_redeem_amount": pending.redeem_amount,
                }
            )
            pending.unlink()

    def _elitepoints_apply_refund_source(self):
        """A refund order never carries its own elitepoints_customer_ref —
        that field (like every field this module adds to pos.order) is
        copy=False, and core's own _prepare_refund_values() has no idea
        this module exists. Without this, _sync_elitepoints_reversal below
        would have no customer to reverse anything for, and a refund of an
        ElitePoints-synced sale would silently do nothing — the exact gap
        this whole method exists to close.

        refunded_order_id is a non-stored compute (lines.refunded_orderline_id
        .order_id), not something staged ahead of time, so this has to run
        after the order and its lines both exist — same reason
        _elitepoints_apply_pending_sync runs from create()/write() rather
        than at the field-definition level.
        """
        for order in self:
            if order.elitepoints_customer_ref:
                continue
            original = order.refunded_order_id
            if not original or original.elitepoints_sync_status != "synced":
                continue
            order.write(
                {
                    "elitepoints_customer_ref": original.elitepoints_customer_ref,
                    "elitepoints_identifier": original.elitepoints_identifier,
                }
            )

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._elitepoints_apply_pending_sync()
        orders._elitepoints_apply_refund_source()
        orders._elitepoints_dispatch_sync()
        return orders

    def write(self, vals):
        result = super().write(vals)
        if vals.get("state") in SYNCABLE_STATES:
            self._elitepoints_apply_pending_sync()
            self._elitepoints_apply_refund_source()
            self._elitepoints_dispatch_sync()
        return result

    def _elitepoints_dispatch_sync(self):
        """Routes each order to the sync it actually needs: a refund order
        (refunded_order_id set) reverses the sale it refunds; anything else
        with a customer attached earns or redeems normally. An order that is
        neither — no customer looked up, or a refund of a sale that was
        never itself ElitePoints-synced — gets no sync call at all; its
        status is set by whichever method would have handled it, the same
        way it always was for a plain order with no customer.
        """
        for order in self:
            if order.state not in SYNCABLE_STATES:
                continue
            if order.elitepoints_sync_status not in ("not_applicable", "pending", "failed", False):
                continue
            if order.refunded_order_id:
                order.elitepoints_sync_status = "pending"
                order._sync_elitepoints_reversal()
            elif order.elitepoints_customer_ref:
                order.elitepoints_sync_status = "pending"
                order._sync_elitepoints()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def _elitepoints_build_items(self):
        self.ensure_one()
        # Exclude the ElitePoints Redemption line itself: it's a synthetic
        # negative-priced order line the frontend adds purely so the POS
        # cart total reflects the discount (see control_buttons.js) — not
        # a real purchased item. Sending it as an "item" fails backend
        # validation ("unitPrice must not be less than 0"), and would be
        # wrong even if it passed: the backend's own redeem_points call
        # already derives the discount from gross_amount/redeem_amount
        # separately, so including it here would double-count it.
        redeem_product = self.env.ref(
            "elitepoints_loyalty.product_elitepoints_redeem", raise_if_not_found=False
        )
        items = []
        for line in self.lines:
            if redeem_product and line.product_id.id == redeem_product.id:
                continue
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
            pos_config = order.config_id
            external_transaction_id = order.pos_reference or f"pos-order-{order.id}"
            items = order._elitepoints_build_items()
            payment_method = order._elitepoints_payment_method_label()

            try:
                if order.elitepoints_redeem_amount > 0:
                    gross_amount = order.amount_total + order.elitepoints_redeem_amount
                    result = client.redeem_points(
                        pos_config,
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
                    # A redemption is never a pure deduction: the backend
                    # also earns points on whatever the customer still paid
                    # (amount - redeem_amount) and applies it to the real
                    # balance in the same call. Confirmed live that this
                    # was being computed correctly all along but silently
                    # dropped before it reached here — every redemption
                    # order showed 0 points earned even though the
                    # customer's actual balance had correctly gone up. The
                    # backend's redeem_points response didn't return this
                    # field at all until ElitePoint-Backend PR #202, which
                    # is the other half of this fix.
                    order.elitepoints_points_earned = result.get(
                        "pointsEarned", 0
                    )
                else:
                    result = client.earn_points(
                        pos_config,
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

    def _sync_elitepoints_reversal(self):
        """Posts a refund order to ElitePoints as a reversal of the sale it
        refunds. Mirrors _sync_elitepoints's own promise not to raise: a
        network hiccup must not block a cashier who has already handed the
        money back. Failures are recorded the same way and picked up by the
        same retry cron.
        """
        client = self.env["elitepoints.client"]
        for order in self:
            if order.elitepoints_sync_status == "synced":
                continue
            original = order.refunded_order_id
            if not order.elitepoints_customer_ref or not original:
                order.elitepoints_sync_status = "not_applicable"
                continue
            if order.elitepoints_sync_attempts >= MAX_SYNC_ATTEMPTS:
                continue

            order.elitepoints_sync_attempts += 1
            pos_config = order.config_id
            original_external_transaction_id = (
                original.pos_reference or f"pos-order-{original.id}"
            )
            # The refund order's own pos_reference is copied from the order
            # it refunds (core's _prepare_refund_values), so it collides
            # with the original and can't double as this reversal's own
            # idempotency key. uuid is always freshly generated for a
            # refund order by that same method, so it uniquely identifies
            # THIS reversal instead.
            external_transaction_id = order.uuid

            try:
                result = client.reverse_transaction(
                    pos_config,
                    original_external_transaction_id=original_external_transaction_id,
                    refund_amount=abs(order.amount_total),
                    description=f"Refund of {original.pos_reference or original.name}",
                    external_transaction_id=external_transaction_id,
                )
                # Signed to match how the backend records its own reversal
                # transaction: negative because these are points leaving
                # the earned total / re-entering the balance, not a fresh
                # earn or redeem in their own right.
                order.elitepoints_points_earned = -result.get(
                    "pointsClawedBack", 0
                )
                order.elitepoints_points_redeemed = -result.get(
                    "pointsReturned", 0
                )
                order.elitepoints_sync_status = "synced"
                order.elitepoints_sync_error = False
            except Exception as exc:  # noqa: BLE001 - must never propagate
                _logger.error(
                    "ElitePoints reversal sync failed for POS refund order %s (attempt %s): %s",
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
        if not failed_orders:
            return

        _logger.info(
            "Retrying ElitePoints sync for %s order(s)", len(failed_orders)
        )
        refunds = failed_orders.filtered(lambda o: o.refunded_order_id)
        (failed_orders - refunds)._sync_elitepoints()
        refunds._sync_elitepoints_reversal()

    # ------------------------------------------------------------------
    # RPC surface for the POS frontend
    # ------------------------------------------------------------------

    @api.model
    def elitepoints_stage_order_sync(
        self, order_uuid, customer_ref, identifier, redeem_amount
    ):
        """Stages customer/redemption data for an order that hasn't
        reached the backend yet, keyed by the order's client-generated
        UUID. See the note above _elitepoints_apply_pending_sync for why
        this exists instead of a plain synced field.
        """
        pending_model = self.env["elitepoints.pending.sync"].sudo()
        pending_model.search([("order_uuid", "=", order_uuid)]).unlink()
        pending_model.create(
            {
                "order_uuid": order_uuid,
                "customer_ref": customer_ref,
                "identifier": identifier,
                "redeem_amount": redeem_amount or 0.0,
            }
        )

    @api.model
    def elitepoints_lookup_customer(
        self, pos_config_id, identifier, id_type, first_name=None, last_name=None
    ):
        pos_config = self.env["pos.config"].browse(pos_config_id)
        return self.env["elitepoints.client"].lookup_customer(
            pos_config, identifier, id_type, first_name=first_name, last_name=last_name
        )

    @api.model
    def elitepoints_get_balance(self, pos_config_id, customer_ref):
        pos_config = self.env["pos.config"].browse(pos_config_id)
        return self.env["elitepoints.client"].get_customer_balance(
            pos_config, customer_ref
        )

    @api.model
    def elitepoints_get_redeem_product_id(self):
        # The redemption line's underlying product is shared across every
        # shop — it's just a generic line item used to render the discount,
        # not an ElitePoints-identity concept, so it isn't per pos.config.
        product = self.env.ref(
            "elitepoints_loyalty.product_elitepoints_redeem", raise_if_not_found=False
        )
        return product.id if product else False

    @api.model
    def elitepoints_is_configured(self, pos_config_id):
        pos_config = self.env["pos.config"].browse(pos_config_id)
        return self.env["elitepoints.client"].is_configured(pos_config)
