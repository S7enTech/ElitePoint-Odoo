/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

// Defensive workaround, not a fix: as of Odoo 18.0, PosOrder.taxTotals can
// throw ("Cannot read properties of undefined (reading 'filter')" on
// this.payment_ids) on a freshly opened POS register, on completely vanilla
// Odoo with no custom modules installed. Confirmed by building Odoo 18
// straight from its own official GitHub source with nothing else installed.
// See https://github.com/S7enTech/ElitePoint-Odoo/blob/main/README.md for
// the full isolation writeup. Without this, that core crash takes down the
// whole POS screen (including this module's own control button) before a
// cashier ever sees it. This only ever activates when the underlying
// computation throws — normal behavior is untouched otherwise. Safe to
// delete once the upstream issue is fixed.
const FALLBACK_TAX_TOTALS = {
    order_sign: 1,
    order_total: 0,
    order_rounding: 0,
    order_remaining: 0,
    order_has_zero_remaining: true,
    total_amount_currency: 0,
    base_amount_currency: 0,
    tax_amount_currency: 0,
    subtotals: [],
};

patch(PosOrder.prototype, {
    get taxTotals() {
        try {
            return super.taxTotals;
        } catch (error) {
            console.error(
                "[elitepoints_loyalty] PosOrder.taxTotals threw; falling back to a zeroed " +
                    "total so the POS screen doesn't hard-crash. Not caused by this module — " +
                    "see static/src/js/pos_order_patch.js.",
                error
            );
            return { ...FALLBACK_TAX_TOTALS };
        }
    },
});
