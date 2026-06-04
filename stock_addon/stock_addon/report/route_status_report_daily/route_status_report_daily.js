// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.query_reports["Route Status Report Daily"] = {
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
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "cost_center",
			label: __("Route (Cost Center)"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => {
				const company = frappe.query_report.get_filter_value("company");
				return { filters: { company: company } };
			},
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.is_total) {
			value = `<span style="font-weight:600">${value}</span>`;
		}
		return value;
	},

	onload: function (report) {
		report.page.add_inner_button(__("Print PDF"), function () {
			const data = report.data || [];
			if (!data.length) {
				frappe.msgprint(__("Run the report first."));
				return;
			}
			frappe.call({
				method: "stock_addon.stock_addon.report.route_status_report_daily.route_status_report_daily.get_pdf_html",
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
