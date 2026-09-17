frappe.ui.form.on("Landed Cost Voucher", {
    refresh_fields: function(frm) {
        frm.refresh_field("purchase_receipts");
    },
    refresh: function(frm) {
        // Add debug log for refresh
        console.log("LCV: Form refreshed", frm);
        frm.fields_dict['purchase_receipts'].grid.get_field('receipt_document').get_query = function(doc, cdt, cdn) {
            console.log("LCV: get_query called for receipt_document", {doc, cdt, cdn});
            return {
                query: "stock_addon.stock_addon.overrides.landed_cost_voucher_override.purchase_receipt_query",
                filters: {}
            };
        };
    }
});

frappe.ui.form.on("Landed Cost Purchase Receipt", {
    receipt_document(frm, cdt, cdn) {
        var d = locals[cdt][cdn];
        console.log("LCV: receipt_document event triggered", d);
        if (d.receipt_document) {
            frappe.call({
                method: "stock_addon.stock_addon.overrides.landed_cost_voucher_override.get_receipt_document_details",
                args: {
                    receipt_document: d.receipt_document,
                    receipt_document_type: d.receipt_document_type,
                },
                callback: function (r) {
                    console.log("LCV: get_receipt_document_details response", r);
                    if (r.message) {
                        $.extend(d, r.message);
                        refresh_field("purchase_receipts");
                    }
                },
                error: function(err) {
                    console.error("LCV: get_receipt_document_details error", err);
                }
            });
        }
    },
    onload: function(frm) {
        console.log("LCV: Landed Cost Purchase Receipt child table loaded", frm);
    },
    supplier_delivery_note_input: function(frm, cdt, cdn) {
        var d = locals[cdt][cdn];
        if (d.supplier_delivery_note_input) {
            frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: "Purchase Receipt",
                    filters: {
                        supplier_delivery_note: d.supplier_delivery_note_input
                    },
                    fields: ["name"]
                },
                callback: function(r) {
                    if (r.message && r.message.length) {
                        d.receipt_document = r.message[0].name;
                        refresh_field("purchase_receipts");
                    } else {
                        frappe.msgprint("No Purchase Receipt found for this Supplier Delivery Note.");
                    }
                }
            });
        }
    }
});

// ---------------------------------------------------------------------------
// Landed cost accounts filled in, uncharged lines dropped, VAT Paid accounts
//
// A new voucher starts with a Landed Cost line for every account ticked
// "Landed Cost Account" on the chart of accounts, so nobody looks them up.
// Lines still at zero are dropped when the voucher is saved (the server drops
// them too, for vouchers saved some other way). The VAT Paid accounts start as
// the ones the company's last voucher used.
//
// Inside an IIFE so none of these names leak into the scope this file shares.
// ---------------------------------------------------------------------------
(function () {
    const DEFAULTS_METHOD =
        "stock_addon.stock_addon.doctype.landed_cost_voucher.landed_cost_voucher.get_landed_cost_defaults";
    const VAT_ACCOUNT_FIELDS = ["custom_prepaid_vat_account", "custom_vat_control_account"];

    function fill_new_voucher(frm) {
        if (!frm.is_new() || !frm.doc.company) return;

        const company = frm.doc.company;
        const docname = frm.doc.name;

        frappe.call({ method: DEFAULTS_METHOD, args: { company } }).then((r) => {
            // Only on the voucher and company it was asked for; an answer for a
            // company since changed away from is dropped.
            if (frm.doc.name !== docname || frm.doc.company !== company) return;
            const { accounts = [], vat_accounts = {} } = (r && r.message) || {};

            // Lines nobody has priced make way: the blank line a new form opens
            // with, and the previous company's accounts after a company change.
            (frm.doc.taxes || [])
                .filter((row) => !flt(row.amount))
                .forEach((row) => frappe.model.clear_doc(row.doctype, row.name));

            const present = new Set((frm.doc.taxes || []).map((row) => row.expense_account));
            accounts
                .filter((account) => !present.has(account.name))
                .forEach((account) => {
                    const row = frm.add_child("taxes", { description: account.account_name, amount: 0 });
                    // set_value rather than a plain field, so ERPNext's own handler
                    // fills in the account currency and exchange rate
                    frappe.model.set_value(row.doctype, row.name, "expense_account", account.name);
                });
            frm.refresh_field("taxes");

            VAT_ACCOUNT_FIELDS.forEach((fieldname) => {
                if (!frm.doc[fieldname] && vat_accounts[fieldname]) {
                    frm.set_value(fieldname, vat_accounts[fieldname]);
                }
            });
        });
    }

    function drop_uncharged_lines(frm) {
        if (frm.doc.docstatus !== 0) return;
        const lines = frm.doc.taxes || [];
        const charged = lines.filter((row) => flt(row.amount));

        if (!charged.length) {
            // Dropping them all would lose the list; ask for an amount instead.
            frappe.msgprint({
                title: __("No landed cost entered"),
                message: __("Enter an amount on at least one Landed Cost line."),
                indicator: "orange",
            });
            frappe.validated = false;
            return;
        }
        if (charged.length === lines.length) return;

        lines
            .filter((row) => !flt(row.amount))
            .forEach((row) => frappe.model.clear_doc(row.doctype, row.name));
        frm.refresh_field("taxes");
    }

    frappe.ui.form.on("Landed Cost Voucher", {
        setup(frm) {
            VAT_ACCOUNT_FIELDS.forEach((fieldname) => {
                frm.set_query(fieldname, () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
            });
        },

        onload(frm) {
            fill_new_voucher(frm);
        },

        company(frm) {
            if (!frm.is_new()) return;
            // The VAT accounts chosen belong to the previous company.
            VAT_ACCOUNT_FIELDS.forEach((fieldname) => {
                if (frm.doc[fieldname]) frm.set_value(fieldname, "");
            });
            fill_new_voucher(frm);
        },

        validate(frm) {
            drop_uncharged_lines(frm);
        },
    });
})();