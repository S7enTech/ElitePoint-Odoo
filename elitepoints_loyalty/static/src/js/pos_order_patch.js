/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

// Defensive workaround, not a fix: as of Odoo 18.0, PosOrder.setup() defaults
// this.lines to [] when missing ("if (!vals.lines) { this.lines = []; }")
// but has no equivalent default for this.payment_ids — which is then read
// unguarded (`.filter`, `.map`, `.reduce`, `.find`, `.some`, `.length`) in
// well over a dozen places across point_of_sale/app/models/pos_order.js. On
// a freshly opened POS register this leaves payment_ids undefined, and both
// the initial render (taxTotals, getCustomerDisplayData) and the very first
// product tap (get_total_paid, via recomputeOrderData) throw "Cannot read
// properties of undefined" before a cashier — or this module's own control
// button — is ever reachable. Confirmed on completely vanilla Odoo 18 built
// straight from its own official GitHub source with no custom modules
// installed; see the README for the full isolation writeup.
//
// IMPORTANT: do not "fix" this by assigning this.payment_ids = [] anywhere
// (in setup(), recomputeOrderData(), or otherwise). That was tried and
// reliably broke something else: newly created pos.order.line records
// stopped showing up in order.lines at all — a product tap silently did
// nothing, no crash, no cart update. (The actual cause of that turned out
// to be unrelated to this file — see models/pos_order.py's
// _load_pos_data_fields for the real story — but the assignment pattern is
// still worth avoiding here on general principle: writing to a reactive
// relational field mid-flow is exactly the kind of thing that trips this
// class of bug.) The only method here that actually touches payment_ids
// directly is get_total_paid() — get_total_tax/get_total_with_tax/
// get_change all derive from taxTotals instead, which is already guarded
// below. So every guard in this file is read-only: call super, catch,
// return a fallback. Never assign. Safe to delete once the upstream issue
// is fixed.
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

    // Same root cause (this.payment_ids undefined), different call site:
    // getCustomerDisplayData does `this.payment_ids.map(...)` directly.
    // Fires repeatedly (every order change resends to the customer-facing
    // display), so left unguarded it re-crashes continuously.
    getCustomerDisplayData() {
        try {
            return super.getCustomerDisplayData();
        } catch (error) {
            console.error(
                "[elitepoints_loyalty] PosOrder.getCustomerDisplayData threw; returning a " +
                    "safe empty payload so the customer display doesn't hard-crash the POS " +
                    "screen. Not caused by this module — see static/src/js/pos_order_patch.js.",
                error
            );
            return {
                lines: [],
                finalized: this.finalized,
                amount: "",
                paymentLines: [],
                change: false,
                generalNote: this.general_note || "",
                qrPaymentData: undefined,
            };
        }
    },

    // Same root cause, hit via recomputeOrderData() — called after every
    // order mutation (adding a line, changing a payment, etc.), including
    // the very first product tap on a fresh order.
    get_total_paid() {
        try {
            return super.get_total_paid();
        } catch (error) {
            console.error(
                "[elitepoints_loyalty] PosOrder.get_total_paid threw; falling back to 0 so " +
                    "adding a product to a fresh order doesn't hard-crash. Not caused by this " +
                    "module — see static/src/js/pos_order_patch.js.",
                error
            );
            return 0;
        }
    },
});
