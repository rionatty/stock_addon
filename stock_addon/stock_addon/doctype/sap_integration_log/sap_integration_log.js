// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.ui.form.on("SAP Integration Log", {
	refresh(frm) {
		const doctype = frm.doc.reference_doctype;
		const name = frm.doc.reference_name;

		if (doctype && name) {
			frm.add_custom_button(__("Open {0}", [__(doctype)]), () =>
				frappe.set_route("Form", doctype, name)
			);
		}

		// Only a failure tied to a real document can be resent.
		if (frm.doc.status !== "Failed" || !doctype || !name) return;

		frm.add_custom_button(__("Resend to SAP"), () => {
			frappe.call({
				method: "stock_addon.stock_addon.sap_integration.transactions.resend_from_log",
				args: { log_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Sending {0} to SAP...", [name]),
				callback(r) {
					frappe.msgprint({
						title: __("Resend"),
						message: frappe.utils.escape_html(r.message || __("Done")),
						indicator: "green",
					});
				},
			});
		}).addClass("btn-primary");
	},
});
