/** @odoo-module */

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const LOOKUP_TYPES = {
    phone: "phone",
    email: "email",
    barcode: "barcode",
};

/**
 * Lets a cashier look up a customer's ElitePoints balance and decide how
 * much of it to redeem against the current order. Confirming does not talk
 * to ElitePoints directly for the redemption itself — it only adds a
 * negative-priced order line for the chosen amount. The actual earn/redeem
 * call happens once, after the order is paid (see models/pos_order.py),
 * so the amount ElitePoints is told about always matches what the customer
 * was actually charged, even if the cart changes after this dialog closes.
 */
export class ElitePointsDialog extends Component {
    static template = "elitepoints_loyalty.ElitePointsDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        onConfirm: Function,
        getPayload: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            identifier: "",
            lookupType: LOOKUP_TYPES.phone,
            loading: false,
            customer: null,
            error: null,
            redeemAmount: 0,
        });
    }

    setLookupType(type) {
        this.state.lookupType = type;
    }

    async lookupCustomer() {
        if (!this.state.identifier.trim()) {
            return;
        }
        this.state.loading = true;
        this.state.error = null;
        try {
            const customer = await this.orm.call(
                "pos.order",
                "elitepoints_lookup_customer",
                [
                    this.props.getPayload().posConfigId,
                    this.state.identifier.trim(),
                    this.state.lookupType,
                ]
            );
            this.state.customer = customer;
            this.state.redeemAmount = 0;
        } catch (error) {
            this.state.customer = null;
            this.state.error =
                (error && error.data && error.data.message) ||
                _t("Could not find that customer.");
        } finally {
            this.state.loading = false;
        }
    }

    get maxRedeemable() {
        if (!this.state.customer) {
            return 0;
        }
        return Math.min(
            this.state.customer.pointsBalance || 0,
            this.props.getPayload().orderTotal
        );
    }

    confirm() {
        if (!this.state.customer) {
            return;
        }
        const redeemAmount = Math.max(
            0,
            Math.min(Number(this.state.redeemAmount) || 0, this.maxRedeemable)
        );
        // props.close is the dialog service's own dismiss function — it's
        // always injected as `{...props, close}` by dialog_service.js,
        // silently overwriting anything passed under that name, and it
        // takes no arguments. Getting data back out requires a
        // separately-named callback instead.
        this.props.onConfirm({
            customerRef: String(this.state.customer.customerId),
            identifier: this.state.identifier.trim(),
            pointsBalance: this.state.customer.pointsBalance,
            redeemAmount,
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
