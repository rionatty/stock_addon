// Sales Invoice — Return Reason behaviour, and the warehouse and cost center
// that follow the sales person (second half of this file)
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

// ---------------------------------------------------------------------------
// Warehouse and cost center from the sales person
//
// Choosing a customer brings their sales team onto the invoice (ERPNext copies
// it from the Customer). The invoice then takes that sales person's warehouse
// and cost center, by the rule the Sales Pro app applies to every invoice it
// sends (stock_addon/stock_addon/sales_invoice_defaults.py). Changing the
// sales person moves both to the new rep's.
//
// Both go on the header AND on every item row. ERPNext copies Source Warehouse
// to all rows itself, but copies a header Cost Center only into rows that have
// none (copy_value_in_all_rows) — and a row takes the company's default cost
// center the moment an item is picked, so the header alone would not reach it.
//
// Inside an IIFE so none of these names leak into the scope this file shares.
// ---------------------------------------------------------------------------
(function () {
    const DEFAULTS_METHOD = "stock_addon.stock_addon.sales_invoice_defaults.get_sales_person_defaults";

    // Expiry and Damaged credit notes go to the return warehouses (above).
    const RETURN_WAREHOUSE_REASONS = ["Expiry", "Damaged"];

    const POLL_MS = 150;
    const QUIET_TICKS = 3; // ~450 ms with nothing in flight
    const GIVE_UP_TICKS = 70; // ~10 s

    // The first Sales Team row naming a sales person speaks for the invoice —
    // the same row the rest of the addon reads.
    function lead_sales_person(frm) {
        const row = (frm.doc.sales_team || []).find((r) => r.sales_person);
        return row ? row.sales_person : null;
    }

    // Whether a change to this row can change who leads: no row above it names
    // a sales person. Decided from the event itself, not from anything
    // remembered — desk reuses one form object for every invoice opened, and
    // runs onload only the first time each one is opened.
    function in_lead_position(frm, row_name) {
        const rows = frm.doc.sales_team || [];
        const at = rows.findIndex((r) => r.name === row_name);
        return at !== -1 && rows.slice(0, at).every((r) => !r.sales_person);
    }

    function applicable(frm) {
        // Once this file has loaded, its Sales Team handlers also fire on every
        // other form with a sales team (Sales Order, Delivery Note, Customer).
        // A POS invoice takes both values from its POS Profile.
        return frm.doctype === "Sales Invoice" && frm.doc.docstatus === 0 && !frm.doc.is_pos;
    }

    // Run fn once nothing has been in flight for a short quiet spell. When the
    // customer changes, that is after ERPNext has loaded their details and
    // sales team; reading the team any sooner finds the previous one.
    function when_settled(frm, token, fn) {
        let quiet = 0;
        let ticks = 0;
        const tick = () => {
            if (frm.__sa_rep_token !== token) return; // a newer change took over
            if (++ticks > GIVE_UP_TICKS) return;
            const busy =
                ((frappe.request && frappe.request.ajax_count) || 0) > 0 || !!frm.updating_party_details;
            quiet = busy ? 0 : quiet + 1;
            if (quiet >= QUIET_TICKS) return fn();
            setTimeout(tick, POLL_MS);
        };
        setTimeout(tick, POLL_MS);
    }

    // Fill from whoever leads once things have settled. A newer call replaces a
    // pending one, and a wait that outlives this invoice (the user opened
    // another) does not land on the next.
    function schedule(frm) {
        if (!applicable(frm)) return;
        const token = (frm.__sa_rep_token = (frm.__sa_rep_token || 0) + 1);
        const docname = frm.doc.name;

        when_settled(frm, token, () => {
            if (frm.doc.name === docname && applicable(frm)) load_defaults(frm);
        });
    }

    function load_defaults(frm) {
        const docname = frm.doc.name;
        const asked = { sales_person: lead_sales_person(frm), company: frm.doc.company };
        if (!asked.sales_person || !asked.company) return; // nothing to go by: leave the fields as they are

        frappe.call({ method: DEFAULTS_METHOD, args: asked }).then((r) => {
            // The reply lands only on the invoice, sales person and company it
            // was asked for; if the form has moved on, a newer call follows.
            if (frm.doc.name !== docname || !applicable(frm)) return;
            if (lead_sales_person(frm) !== asked.sales_person || frm.doc.company !== asked.company) return;
            apply_defaults(frm, asked.sales_person, (r && r.message) || {});
        });
    }

    function apply_defaults(frm, sales_person, { warehouse, cost_center }) {
        const set = [];
        const keep_return_warehouse =
            frm.doc.is_return && RETURN_WAREHOUSE_REASONS.includes(frm.doc.custom_return_reason);

        if (warehouse && !keep_return_warehouse && set_everywhere(frm, "set_warehouse", "warehouse", warehouse)) {
            set.push(__("Warehouse {0}", [warehouse]));
        }
        if (cost_center && set_everywhere(frm, "cost_center", "cost_center", cost_center)) {
            set.push(__("Cost Center {0}", [cost_center]));
        }

        if (set.length) {
            frappe.show_alert(
                { message: __("From Sales Person {0}: {1}", [sales_person, set.join(", ")]), indicator: "blue" },
                5
            );
        }
    }

    // Header field plus the same value on every item row; true if anything changed.
    function set_everywhere(frm, header_field, row_field, value) {
        let changed = false;
        if (frm.doc[header_field] !== value) {
            frm.set_value(header_field, value);
            changed = true;
        }
        (frm.doc.items || []).forEach((row) => {
            if (row[row_field] !== value) {
                frappe.model.set_value(row.doctype, row.name, row_field, value);
                changed = true;
            }
        });
        return changed;
    }

    frappe.ui.form.on("Sales Invoice", {
        // A new customer always brings their rep's values, even when the rep
        // is the one already on the invoice.
        customer(frm) {
            if (frm.doc.customer) schedule(frm);
        },
    });

    // Only a change to who leads moves the values: editing or removing a second
    // row leaves what is on the invoice alone.
    frappe.ui.form.on("Sales Team", {
        sales_person(frm, cdt, cdn) {
            if (in_lead_position(frm, cdn)) schedule(frm);
        },

        // "before": the row is still in the table, so its position can be read.
        before_sales_team_remove(frm, cdt, cdn) {
            const row = (frm.doc.sales_team || []).find((r) => r.name === cdn);
            if (row && row.sales_person && in_lead_position(frm, cdn)) schedule(frm);
        },
    });
})();
