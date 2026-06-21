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

        if (frm.doc.is_return && frm.doc.custom_return_reason === "Expiry") {
            frappe.confirm(
                __("Set all item rates to 50% and route goods to the Reparking Warehouse?"),
                () => _apply_expiry_adjustments(frm)
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

function _apply_expiry_adjustments(frm) {
    frappe.db
        .get_single_value("Stock Addon Settings", "reparking_warehouse")
        .then((warehouse) => {
            if (!warehouse) {
                frappe.msgprint({
                    title: __("Reparking Warehouse not set"),
                    message: __(
                        "Please configure the Reparking Warehouse in <b>Stock Addon Settings</b> first."
                    ),
                    indicator: "orange",
                });
                return;
            }

            frm.doc.items.forEach((item) => {
                item.rate = flt(item.rate) / 2;
                item.amount = flt(item.qty) * item.rate;
                item.warehouse = warehouse;
            });

            frm.refresh_field("items");

            // Let ERPNext recalculate taxes and totals
            if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
                frm.cscript.calculate_taxes_and_totals();
            }

            frm.dirty();

            frappe.show_alert(
                {
                    message: __("Rates halved · Warehouse set to {0}", [warehouse]),
                    indicator: "blue",
                },
                5
            );
        });
}
