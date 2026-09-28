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
            close: (result) => this._onElitePointsDialogClosed(order, result),
        });
    },

    _onElitePointsDialogClosed(order, result) {
        if (!result) {
            return;
        }

        order.elitepoints_customer_ref = result.customerRef;
        order.elitepoints_identifier = result.identifier;

        // Remove a previous redemption line from an earlier lookup on the
        // same order before adding the new one, so re-opening the dialog
        // never stacks up multiple discount lines.
        const existingLine = order
            .get_orderlines()
            .find((line) => line.elitepoints_redeem_line);
        if (existingLine) {
            order.removeOrderline(existingLine);
        }

        order.elitepoints_redeem_amount = result.redeemAmount || 0;

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
            const line = order.add_product(rewardProduct, {
                price: -result.redeemAmount,
                quantity: 1,
                merge: false,
            });
            if (line) {
                line.elitepoints_redeem_line = true;
            }
        }
    },
});
