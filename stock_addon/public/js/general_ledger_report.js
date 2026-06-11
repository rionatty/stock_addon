// stock_addon — adds the uniform "Print PDF" button to the STANDARD
// General Ledger query report. ERPNext owns that report's .js, so this file
// is loaded desk-wide via app_include_js and watches the route instead.
(function () {
	const REPORT_NAME = "General Ledger";
	const PRINT_METHOD =
		"stock_addon.stock_addon.report_patches.general_ledger_get_pdf_html";

	function on_route_change() {
		const route = frappe.get_route ? frappe.get_route() : [];
		if (route[0] !== "query-report" || route[1] !== REPORT_NAME) return;
		add_button_when_ready(0);
	}

	function add_button_when_ready(tries) {
		const report = frappe.query_report;
		if (!report || !report.page || !report.page.inner_toolbar) {
			// the query report page builds asynchronously after the route fires
			if (tries < 40) setTimeout(() => add_button_when_ready(tries + 1), 250);
			return;
		}

		// the user may have navigated away while we were waiting
		const route = frappe.get_route();
		if (route[0] !== "query-report" || route[1] !== REPORT_NAME) return;

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

	$(document).ready(function () {
		if (frappe.router && frappe.router.on) {
			frappe.router.on("change", on_route_change);
		}
		// cover the initial page load (router "change" only fires on navigation)
		if (frappe.after_ajax) {
			frappe.after_ajax(on_route_change);
		}
	});
})();
