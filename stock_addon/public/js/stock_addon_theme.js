// Stock Addon — global desk JS (ported from rionatty/Agricalt `twiga`).
// Status indicator colours for Stock Addon doctypes. Wrapped in an IIFE
// so nothing here collides with the agriculture app's copy when both
// apps are installed on one site.

(function () {
	frappe.provide("stock_addon");

	const STATUS_COLORS = {
		// Generic workflow
		"Draft":     "gray",
		"Submitted": "blue",
		"Approved":  "green",
		"Rejected":  "red",
		"Cancelled": "red",
		"Completed": "green",
		"Open":      "orange",
		"In Progress": "blue",
		// Field Expense
		"Posted":    "green",
		// SAP sync
		"Pending":   "yellow",
		"Success":   "green",
		"Failed":    "red",
		"Synced":    "green",
	};

	stock_addon.get_status_color = (status) => STATUS_COLORS[status] || "gray";

	// Apply an indicator dot to the status field on our own forms
	const STATUS_DOCTYPES = ["Field Expense", "SAP Integration Log"];

	STATUS_DOCTYPES.forEach((dt) => {
		frappe.ui.form.on(dt, {
			refresh(frm) {
				const status = frm.doc.status;
				if (!status) return;
				frm.page.set_indicator(status, stock_addon.get_status_color(status));
			},
		});
	});
})();
