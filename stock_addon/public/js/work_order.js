// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt
//
// Work Order: "Generate Batch" button.
// Creates a batch (BAT:NNN MFG:dd.MM.yyyy <item>) for the production item
// and stamps it on the Work Order. The Manufacture Stock Entry then picks
// the batch up automatically (see doc_events/stock_entry.py).
//
// Installed as a database Client Script on migrate (client_scripts.py) so
// no bench build is required.

frappe.ui.form.on("Work Order", {
	refresh(frm) {
		if (frm.doc.docstatus === 2) return;

		if (frm.doc.custom_batch_number) {
			frm.dashboard.set_headline(
				__("Batch: {0}", [frappe.utils.escape_html(frm.doc.custom_batch_number)])
			);
			return;
		}

		frm.add_custom_button(__("Generate Batch"), () => {
			frappe.call({
				method: "stock_addon.stock_addon.doctype.work_order.work_order.generate_batch_for_work_order",
				args: {
					work_order: frm.doc.name,
					item: frm.doc.production_item,
				},
				freeze: true,
				freeze_message: __("Generating Batch..."),
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Batch generated: {0}", [frappe.utils.escape_html(r.message)]),
							indicator: "green",
						});
					}
					frm.reload_doc();
				},
			});
		}).addClass("btn-primary");
	},
});
