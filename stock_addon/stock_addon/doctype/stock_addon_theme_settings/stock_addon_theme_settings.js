// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt
//
// Live colour picking. Every change repaints the desk immediately by
// setting the CSS custom properties on :root, so the result is visible
// before saving. Leaving the form without saving puts the saved palette
// back — an unsaved experiment must not follow you around the desk.

// fieldname -> CSS variable. Source of truth is
// stock_addon/stock_addon/theme.py (FIELD_TO_VAR) — keep them in step.
const SA_THEME_FIELDS = {
	primary_navy:          "--agri-primary",
	section_header_colour: "--agri-primary-mid",
	sidebar_background:    "--agri-shell",
	navbar_background:     "--agri-shell-dark",
	canvas_top:            "--agri-canvas-top",
	canvas_bottom:         "--agri-canvas-bottom",
	accent_colour:         "--agri-accent",
	selected_highlight:    "--agri-shell-marker",
	zebra_tint:            "--agri-pale",
	border_colour:         "--agri-border",
};

function sa_relative_luminance(hex) {
	let body = (hex || "").replace("#", "");
	if (body.length === 3) body = body.split("").map((c) => c + c).join("");
	if (body.length !== 6) return null;
	const channels = [0, 2, 4].map((i) => {
		const v = parseInt(body.substr(i, 2), 16) / 255;
		return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
	});
	return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function sa_contrast_with_white(hex) {
	const l = sa_relative_luminance(hex);
	if (l === null) return null;
	return 1.05 / (l + 0.05);
}

function sa_preview(frm) {
	const root = document.documentElement;
	root.classList.toggle("sa-cockpit", !!(frm.doc.enabled && frm.doc.workspace_cockpit));
	Object.entries(SA_THEME_FIELDS).forEach(([fieldname, cssVar]) => {
		const value = (frm.doc[fieldname] || "").trim();
		if (frm.doc.enabled && value) {
			root.style.setProperty(cssVar, value);
		} else {
			root.style.removeProperty(cssVar);
		}
	});
}

function sa_restore_saved() {
	const root = document.documentElement;
	root.classList.toggle("sa-cockpit", !!(frappe.boot && frappe.boot.stock_addon_workspace_cockpit));
	const saved = (frappe.boot && frappe.boot.stock_addon_theme) || {};
	Object.values(SA_THEME_FIELDS).forEach((cssVar) => root.style.removeProperty(cssVar));
	Object.entries(saved).forEach(([cssVar, value]) => root.style.setProperty(cssVar, value));
}

// White text sits on these three, so they are the ones that can become
// unreadable. 4.5:1 is the WCAG AA floor for body text.
function sa_show_contrast(frm) {
	if (!frm.doc.enabled) {
		frm.dashboard.clear_headline();
		return;
	}
	const checks = [
		[__("Canvas (Top)"), frm.doc.canvas_top],
		[__("Canvas (Bottom)"), frm.doc.canvas_bottom],
		[__("Sidebar"), frm.doc.sidebar_background],
	];
	const parts = [];
	let worst = false;
	checks.forEach(([label, colour]) => {
		if (!colour) return;
		const ratio = sa_contrast_with_white(colour);
		if (ratio === null) return;
		const ok = ratio >= 4.5;
		if (!ok) worst = true;
		parts.push(
			`${frappe.utils.escape_html(label)}: <b style="color:${ok ? "var(--green-600,#28a745)" : "var(--red-600,#dc3545)"}">` +
			`${ratio.toFixed(1)}:1</b>`
		);
	});
	if (!parts.length) {
		frm.dashboard.clear_headline();
		return;
	}
	frm.dashboard.set_headline(
		__("White text contrast") + " — " + parts.join(" &nbsp;·&nbsp; ") +
		(worst
			? ` &nbsp; <span style="color:var(--red-600,#dc3545)">${__("Below 4.5:1 — text will be hard to read.")}</span>`
			: ` &nbsp; <span class="text-muted">${__("All above the 4.5:1 readability floor.")}</span>`)
	);
}

const sa_handlers = {
	onload(frm) {
		// Put the saved palette back when navigating away with the
		// preview still applied.
		if (!window.__sa_theme_route_hook) {
			window.__sa_theme_route_hook = true;
			frappe.router.on("change", () => {
				const route = frappe.get_route() || [];
				if (route[1] !== "Stock Addon Theme Settings") sa_restore_saved();
			});
		}
	},

	refresh(frm) {
		sa_preview(frm);
		sa_show_contrast(frm);

		frm.add_custom_button(__("Reset to Defaults"), () => {
			frappe.call({
				method: "stock_addon.stock_addon.doctype.stock_addon_theme_settings.stock_addon_theme_settings.get_default_palette",
				callback(r) {
					if (!r.message) return;
					Object.entries(r.message).forEach(([fieldname, value]) => {
						frm.set_value(fieldname, value);
					});
					frappe.show_alert({
						message: __("Shipped colours restored — save to keep them."),
						indicator: "blue",
					});
				},
			});
		});

		frm.add_custom_button(__("Open a Workspace"), () => frappe.set_route("Workspaces", "Home"));
	},

	after_save(frm) {
		// Keep the client-side copy of the boot palette current, so
		// navigating away restores what was just saved, not what the
		// page loaded with.
		if (frappe.boot) {
			const palette = {};
			Object.entries(SA_THEME_FIELDS).forEach(([fieldname, cssVar]) => {
				const value = (frm.doc[fieldname] || "").trim();
				if (frm.doc.enabled && value) palette[cssVar] = value;
			});
			frappe.boot.stock_addon_theme = palette;
			frappe.boot.stock_addon_workspace_cockpit =
				frm.doc.enabled && frm.doc.workspace_cockpit ? 1 : 0;
		}
		sa_show_contrast(frm);
	},

	enabled(frm) {
		sa_preview(frm);
		sa_show_contrast(frm);
	},

	workspace_cockpit(frm) {
		sa_preview(frm);
	},
};

// Repaint on every colour change
Object.keys(SA_THEME_FIELDS).forEach((fieldname) => {
	sa_handlers[fieldname] = (frm) => {
		sa_preview(frm);
		sa_show_contrast(frm);
	};
});

frappe.ui.form.on("Stock Addon Theme Settings", sa_handlers);
