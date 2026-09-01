// Stock Addon — "Send to SAP" on every document that pushes to SAP.
//
// Documents push automatically on submit; this is for the cases where
// that push failed (bad mapping since corrected, SAP down) or the
// integration was switched on after the document was already submitted.
// The SAP state is shown in the form headline either way, so nobody has
// to open the log to find out whether a document made it across.

(function () {
	if (typeof frappe === "undefined") return;

	// doctype -> is this document one that SAP would accept?
	// Mirrors the on_submit conditions in sap_integration/transactions.py.
	const PUSHABLE = {
		"Sales Invoice": (doc) => doc.docstatus === 1,
		"Material Request": (doc) =>
			doc.docstatus === 1 && doc.material_request_type === "Material Transfer",
		"Payment Entry": (doc) =>
			doc.docstatus === 1 && doc.payment_type === "Receive" && doc.party_type === "Customer",
		"Field Expense": (doc) => doc.status === "Posted",
	};

	function send(frm, force) {
		frappe.call({
			method: "stock_addon.stock_addon.sap_integration.transactions.push_document",
			args: { doctype: frm.doctype, name: frm.doc.name, force: force ? 1 : 0 },
			freeze: true,
			freeze_message: __("Sending to SAP..."),
			callback(r) {
				frappe.msgprint({
					title: __("SAP"),
					message: frappe.utils.escape_html(r.message || __("Done")),
					indicator: "green",
				});
				frm.reload_doc();
			},
		});
	}

	function headline(frm) {
		const status = frm.doc.custom_sap_sync_status;
		const reference = frm.doc.custom_sap_docnum || frm.doc.custom_sap_docentry;
		if (status === "Synced") {
			frm.dashboard.set_headline(
				__("In SAP as document {0}", [frappe.utils.escape_html(reference || "?")])
			);
		} else if (status === "Failed") {
			frm.dashboard.set_headline(
				`<span style="color:var(--red-600,#dc3545)">${__("Not in SAP — the last push failed.")}</span> ` +
				`<a href="/app/sap-integration-log?reference_name=${encodeURIComponent(frm.doc.name)}">${__("See why")}</a>`
			);
		}
	}

	Object.entries(PUSHABLE).forEach(([doctype, is_pushable]) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				if (frm.is_new() || !is_pushable(frm.doc)) return;
				headline(frm);

				const synced = frm.doc.custom_sap_sync_status === "Synced";
				const label = synced ? __("Resend to SAP") : __("Send to SAP");

				const button = frm.add_custom_button(label, () => {
					if (!synced) return send(frm, false);
					// Re-pushing a synced document creates a SECOND one in
					// SAP — make that consequence explicit before allowing it.
					frappe.confirm(
						__("{0} is already in SAP. Sending it again creates a <b>second</b> SAP document. Only do this if the original was removed there. Continue?", [
							frappe.utils.escape_html(frm.doc.name),
						]),
						() => send(frm, true)
					);
				}, __("SAP"));

				if (!synced) button.addClass("btn-primary");
			},
		});
	});
})();
