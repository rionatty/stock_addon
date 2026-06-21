// Sales Invoice — Return Reason behaviour
// Wired via hooks.py  doctype_js["Sales Invoice"]

frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        _toggle_return_fields(frm);
    },

    is_return(frm) {
        _toggle_return_fields(frm);
    },

    custom_return_reason(frm) {
        _toggle_return_fields(frm);

        const reason = frm.doc.custom_return_reason;

        if (!frm.doc.is_return || !reason) return;

        if (reason === "Expiry") {
            frappe.confirm(
                __("Set all item rates to 50% and route goods to the Expiry Return Warehouse?"),
                () => _apply_warehouse_and_price(frm, "reparking_warehouse", true)
            );
        } else if (reason === "Damaged") {
            frappe.confirm(
                __("Route returned goods to the Damaged Return Warehouse?"),
                () => _apply_warehouse_and_price(frm, "damaged_warehouse", false)
            );
        }
    },
});

function _toggle_return_fields(frm) {
    const is_ret = !!frm.doc.is_return;
    frm.toggle_display("custom_return_reason", is_ret);
    frm.toggle_display(
        "custom_return_narration",
        is_ret && frm.doc.custom_return_reason === "Others"
    );
}

/**
 * Fetch the given warehouse setting from Stock Addon Settings, apply it to
 * all item rows, and optionally halve item rates (Expiry only).
 *
 * @param {object} frm          - Frappe form object
 * @param {string} setting_key  - Field name on Stock Addon Settings ("reparking_warehouse" | "damaged_warehouse")
 * @param {boolean} halve_price - Whether to set rates to 50%
 */
function _apply_warehouse_and_price(frm, setting_key, halve_price) {
    frappe.db
        .get_single_value("Stock Addon Settings", setting_key)
        .then((warehouse) => {
            if (!warehouse) {
                const label = setting_key === "reparking_warehouse"
                    ? "Expiry Return Warehouse"
                    : "Damaged Return Warehouse";
                frappe.msgprint({
                    title: __(label + " not set"),
                    message: __(
                        "Please configure <b>{0}</b> in Stock Addon Settings first.",
                        [label]
                    ),
                    indicator: "orange",
                });
                return;
            }

            frm.doc.items.forEach((item) => {
                if (halve_price) {
                    item.rate = flt(item.rate) / 2;
                    item.amount = flt(item.qty) * item.rate;
                }
                item.warehouse = warehouse;
            });

            frm.refresh_field("items");

            // Trigger ERPNext total recalculation
            if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
                frm.cscript.calculate_taxes_and_totals();
            }

            frm.dirty();

            const msg = halve_price
                ? __("Rates halved · Warehouse set to {0}", [warehouse])
                : __("Warehouse set to {0}", [warehouse]);

            frappe.show_alert({ message: msg, indicator: "blue" }, 5);
        });
}
