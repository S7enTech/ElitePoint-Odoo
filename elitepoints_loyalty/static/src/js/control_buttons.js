/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart } from "@odoo/owl";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ElitePointsDialog } from "@elitepoints_loyalty/js/elitepoints_dialog";
import { _t } from "@web/core/l10n/translation";

patch(ControlButtons.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.elitepoints = { configured: false, redeemProductId: false };

        onWillStart(async () => {
            try {
                this.elitepoints.configured = await this.orm.call(
                    "pos.order",
                    "elitepoints_is_configured",
                    []
                );
                if (this.elitepoints.configured) {
                    this.elitepoints.redeemProductId = await this.orm.call(
                        "pos.order",
                        "elitepoints_get_redeem_product_id",
                        []
                    );
                }
            } catch (error) {
                // A failure here should never block the POS from opening.
                this.elitepoints.configured = false;
            }
        });
    },

    get showElitePointsButton() {
        return this.elitepoints.configured;
    },

    async clickElitePoints() {
        const order = this.pos.get_order();
        const orderTotal = order.get_total_with_tax();

        this.dialog.add(ElitePointsDialog, {
            getPayload: () => ({ orderTotal }),
            // NOT `close` — the dialog service always injects its own
            // `close` (dialog_service.js: `subProps: {...props, close}`),
            // silently overwriting anything passed under that name. A
            // confirm callback needs a name the service doesn't reserve.
            onConfirm: (result) => this._onElitePointsDialogClosed(order, result),
        });
    },

    async _onElitePointsDialogClosed(order, result) {
        if (!result) {
            return;
        }

        // pos.order._load_pos_data_fields can't safely be extended in
        // this Odoo build — see models/pos_order.py — so these values
        // can't just be assigned onto the reactive order object and left
        // to ride along with the normal order sync. Instead, stage them
        // server-side now, keyed by this order's UUID (a stock pos.order
        // field that syncs normally); PosOrder.create()/write() picks the
        // row back up once the order itself reaches the backend. Awaited
        // and surfaced on failure — losing this silently would mean the
        // sale completes but never earns/redeems any points, with nothing
        // telling the cashier why.
        try {
            await this.orm.call("pos.order", "elitepoints_stage_order_sync", [
                order.uuid,
                result.customerRef,
                result.identifier,
                result.redeemAmount || 0,
            ]);
        } catch (error) {
            this.notification.add(
                _t(
                    "Could not link this sale to the ElitePoints customer. " +
                        "The sale will still go through, but it won't earn " +
                        "or redeem points — try the lookup again."
                ),
                { type: "danger" }
            );
            return;
        }

        // Remove a previous redemption line from an earlier lookup on the
        // same order before adding the new one, so re-opening the dialog
        // never stacks up multiple discount lines.
        const existingLine = order
            .get_orderlines()
            .find((line) => line.elitepoints_redeem_line);
        if (existingLine) {
            order.removeOrderline(existingLine);
        }

        if (result.redeemAmount > 0) {
            if (!this.elitepoints.redeemProductId) {
                this.notification.add(
                    _t(
                        "ElitePoints redemption product is not available in " +
                            "this POS. Ask your administrator to check the " +
                            "ElitePoints app setup."
                    ),
                    { type: "danger" }
                );
                return;
            }
            const rewardProduct = this.pos.models["product.product"].get(
                this.elitepoints.redeemProductId
            );
            // order.add_product doesn't exist on this Odoo build (verified
            // live — it's undefined on PosOrder); the current API is
            // PosStore.addLineToCurrentOrder. configure=false skips any
            // attribute/configurator popup for this synthetic line.
            const line = await this.pos.addLineToCurrentOrder(
                { product_id: rewardProduct, price_unit: -result.redeemAmount, qty: 1 },
                {},
                false
            );
            if (line) {
                line.elitepoints_redeem_line = true;
            }
        }
    },
});
