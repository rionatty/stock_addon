// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

// Filters mirror ERPNext's standard General Ledger so the wrapped standard
// report (which this builds on) receives exactly the inputs it expects.
frappe.query_reports["General Ledger Running Balance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
			width: "60px",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
			width: "60px",
		},
		{
			fieldname: "account",
			label: __("Account"),
			fieldtype: "MultiSelectList",
			options: "Account",
			get_data: function (txt) {
				return frappe.db.get_link_options("Account", txt, {
					company: frappe.query_report.get_filter_value("company"),
				});
			},
		},
		{
			fieldname: "cost_center",
			label: __("Route / Cost Center"),
			fieldtype: "MultiSelectList",
			options: "Cost Center",
			get_data: function (txt) {
				return frappe.db.get_link_options("Cost Center", txt, {
					company: frappe.query_report.get_filter_value("company"),
				});
			},
		},
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Link",
			options: "Party Type",
			default: "",
			on_change: function () {
				frappe.query_report.set_filter_value("party", "");
			},
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				const party_type = frappe.query_report.get_filter_value("party_type");
				if (!party_type) return [];
				return frappe.db.get_link_options(party_type, txt);
			},
		},
		{
			fieldname: "group_by",
			label: __("Group by"),
			fieldtype: "Select",
			options: [
				"",
				{ value: "Group by Voucher", label: __("Group by Voucher") },
				{
					value: "Group by Voucher (Consolidated)",
					label: __("Group by Voucher (Consolidated)"),
				},
				{ value: "Group by Account", label: __("Group by Account") },
				{ value: "Group by Party", label: __("Group by Party") },
			],
			// blank = one row per GL entry, so the running balance is per entry
			default: "",
		},
		{
			fieldname: "finance_book",
			label: __("Finance Book"),
			fieldtype: "Link",
			options: "Finance Book",
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "MultiSelectList",
			options: "Project",
			get_data: function (txt) {
				return frappe.db.get_link_options("Project", txt, {
					company: frappe.query_report.get_filter_value("company"),
				});
			},
		},
		{
			fieldname: "voucher_no",
			label: __("Voucher No"),
			fieldtype: "Data",
			width: "100px",
		},
	],

	onload: function (report) {
		report.page.add_inner_button(__("Print PDF"), function () {
			const data = report.data || [];
			if (!data.length) {
				frappe.msgprint(__("Run the report first."));
				return;
			}
			frappe.call({
				method: "stock_addon.stock_addon.report.general_ledger_running_balance.general_ledger_running_balance.get_pdf_html",
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
		}).addClass("btn-primary");
	},
};
