// Stock Addon — global desk JS (ported from rionatty/Agricalt `twiga`).
//
// Two jobs:
//   1. Apply the colour overrides from "Stock Addon Theme Settings".
//      They ride along on the session boot (see stock_addon/theme.py),
//      so the desk is painted before first render — no extra request,
//      no flash of the shipped palette.
//   2. Status indicator colours for Stock Addon doctypes.
//
// Wrapped in an IIFE so nothing here collides with the agriculture
// app's copy when both apps are installed on one site.

(function () {
	frappe.provide("stock_addon");

	// ── 1. Theme colour overrides ──────────────────────────────
	stock_addon.apply_palette = function (palette) {
		const root = document.documentElement;
		Object.entries(palette || {}).forEach(([cssVar, value]) => {
			if (value) root.style.setProperty(cssVar, value);
		});
	};

	// The navy workspace cockpit is opt-in: it repaints ERPNext's own
	// workspace layout, which differs between versions, so the standard
	// display is what ships unless the setting asks otherwise.
	stock_addon.apply_cockpit = function (on) {
		document.documentElement.classList.toggle("sa-cockpit", !!on);
	};

	function apply_boot_palette() {
		if (!frappe.boot) return;
		if (frappe.boot.stock_addon_theme) {
			stock_addon.apply_palette(frappe.boot.stock_addon_theme);
		}
		stock_addon.apply_cockpit(frappe.boot.stock_addon_workspace_cockpit);
	}

	apply_boot_palette();               // boot is usually already inlined
	$(document).on("startup", apply_boot_palette);   // belt and braces

	// ── 2. Status indicator colours ────────────────────────────
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
