// stock_addon — adds the uniform "Print PDF" button to the STANDARD
// General Ledger query report. ERPNext owns that report's .js, so this file
// is loaded desk-wide via app_include_js and wraps the report settings'
// onload: Frappe calls onload on every report load, right AFTER it clears
// the inner toolbar, so the button is added at exactly the right moment.
(function () {
	const REPORT_NAME = "General Ledger";
	const PRINT_METHOD =
		"stock_addon.stock_addon.report_patches.general_ledger_get_pdf_html";

	function add_print_button(report) {
		if (!report || !report.page) return;
		const label = __("Print PDF");
		const exists = report.page.inner_toolbar
			.find("button")
			.filter((i, el) => $(el).text().trim() === label).length;
		if (exists) return;

		report.page.add_inner_button(label, () => print_pdf(report)).addClass("btn-primary");
	}

	function print_pdf(report) {
		const data = report.data || [];
		if (!data.length) {
			frappe.msgprint(__("Run the report first."));
			return;
		}
		frappe.call({
			method: PRINT_METHOD,
			args: {
				filters: JSON.stringify(report.get_values()),
				data: JSON.stringify(data),
				columns: JSON.stringify(report.columns || []),
			},
			freeze: true,
			freeze_message: __("Generating PDF..."),
			callback: function (r) {
				if (r.message) {
					const w = window.open();
					w.document.write(r.message);
					w.document.close();
					setTimeout(() => w.print(), 600);
				}
			},
		});
	}

	function wrap_settings(settings) {
		if (!settings || settings._stock_addon_print_wrapped) return settings;
		const original_onload = settings.onload;
		settings.onload = function (report) {
			if (original_onload) original_onload.call(this, report);
			add_print_button(report);
		};
		settings._stock_addon_print_wrapped = true;
		return settings;
	}

	frappe.query_reports = frappe.query_reports || {};

	// Wrap now if ERPNext's general_ledger.js already registered the settings,
	// otherwise intercept the registration the moment it happens (the report
	// .js loads lazily on first visit to the report).
	let stored = wrap_settings(frappe.query_reports[REPORT_NAME]);
	Object.defineProperty(frappe.query_reports, REPORT_NAME, {
		configurable: true,
		enumerable: true,
		get() {
			return stored;
		},
		set(value) {
			stored = wrap_settings(value);
		},
	});

	// Safety net: if the report was already fully loaded before this script
	// ran (e.g. cached settings without a fresh onload), add on route entry.
	function on_route_change() {
		const route = frappe.get_route ? frappe.get_route() : [];
		if (route[0] !== "query-report" || route[1] !== REPORT_NAME) return;
		setTimeout(() => {
			const report = frappe.query_report;
			if (report && report.report_name === REPORT_NAME) add_print_button(report);
		}, 1500);
	}

	$(document).ready(function () {
		if (frappe.router && frappe.router.on) {
			frappe.router.on("change", on_route_change);
		}
		if (frappe.after_ajax) {
			frappe.after_ajax(on_route_change);
		}
	});
})();
