// stock_addon: patches the standard "General Ledger" report to:
//   1. Inject a Customer / Party Name column (before Debit) that shows the
//      customer/supplier name instead of just the party code, resolved from
//      the 'against' field for cash/bank rows that carry no party.
//   2. Add a "Print PDF" inner button that prints the visible rows in the
//      same styled HTML format used by all Stock Addon reports.

frappe.provide("stock_addon");

stock_addon.patch_general_ledger = function () {
	const rname = "General Ledger";
	if (!frappe.query_reports || !frappe.query_reports[rname]) return;
	const report = frappe.query_reports[rname];

	// ── 1.  Party-name column injection ─────────────────────────────────────
	// After the report runs we add a virtual party_name column and populate it.
	const origAfterRender = report.after_render;
	report.after_render = function (...args) {
		if (typeof origAfterRender === "function") origAfterRender.apply(this, args);
		// inject into the report object itself so next render keeps it
		if (cur_report) _inject_party_name(cur_report);
	};

	// ── 2.  Print PDF button ─────────────────────────────────────────────────
	const origOnload = report.onload;
	report.onload = function (report_obj) {
		if (typeof origOnload === "function") origOnload.call(this, report_obj);
		report_obj.page.add_inner_button(__("Print PDF"), function () {
			const data = report_obj.data || [];
			if (!data.length) {
				frappe.msgprint(__("Run the report first."));
				return;
			}
			frappe.call({
				method: "stock_addon.stock_addon.report.general_ledger_running_balance.general_ledger_running_balance.get_pdf_html",
				args: {
					filters: JSON.stringify(report_obj.get_values()),
					data: JSON.stringify(data),
					columns: JSON.stringify(
						(report_obj.columns || []).map((c) =>
							typeof c === "object" ? c : { fieldname: c, label: c }
						)
					),
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
		}).addClass("btn-primary");
	};
};

function _inject_party_name(report_obj) {
	if (!report_obj || !report_obj.data) return;
	const data = report_obj.data;
	const cols = report_obj.columns || [];

	// Don't add twice
	if (cols.some((c) => (typeof c === "object" ? c.fieldname : c) === "party_name"))
		return;

	// Async-resolve party names, update data, refresh grid
	const partyCache = {};
	const customerCache = {};
	const nameField = { Customer: "customer_name", Supplier: "supplier_name", Employee: "employee_name" };

	const resolveAll = async () => {
		for (const row of data) {
			if (!row || typeof row !== "object") continue;
			let name = "";
			if (row.party_type && row.party) {
				const key = row.party_type + "|" + row.party;
				if (partyCache[key] === undefined) {
					const field = nameField[row.party_type];
					if (field) {
						partyCache[key] = await frappe.db.get_value(row.party_type, row.party, field)
							.then((r) => (r && r.message && r.message[field]) ? r.message[field] : row.party)
							.catch(() => row.party);
					} else {
						partyCache[key] = "";
					}
				}
				name = partyCache[key] || "";
			} else if (row.against) {
				// cash/bank rows – take first code from 'against'
				const first = (row.against.split(",")[0] || "").trim();
				if (first) {
					if (customerCache[first] === undefined) {
						customerCache[first] = await frappe.db.get_value("Customer", first, "customer_name")
							.then((r) => (r && r.message && r.message.customer_name) ? r.message.customer_name : "")
							.catch(() => "");
					}
					name = customerCache[first] || "";
				}
			}
			row.party_name = name;
		}

		// Insert party_name column before debit
		const debitIdx = cols.findIndex(
			(c) => (typeof c === "object" ? c.fieldname : c) === "debit"
		);
		const newCol = {
			label: __("Customer / Party Name"),
			fieldname: "party_name",
			fieldtype: "Data",
			width: 200,
		};
		if (debitIdx === -1) {
			cols.push(newCol);
		} else {
			cols.splice(debitIdx, 0, newCol);
		}

		// Trigger grid re-render
		if (report_obj.render_report) {
			report_obj.render_report();
		}
	};

	resolveAll();
}

// Hook into page load — the report definition is registered lazily.
$(document).on("page-change", function () {
	// slight delay so frappe.query_reports is populated
	setTimeout(() => {
		if (frappe.query_reports && frappe.query_reports["General Ledger"]) {
			stock_addon.patch_general_ledger();
		}
	}, 200);
});

// Also run immediately in case the file loads on an already-open GL report
setTimeout(() => {
	if (frappe.query_reports && frappe.query_reports["General Ledger"]) {
		stock_addon.patch_general_ledger();
	}
}, 500);
