// Copyright (c) 2025, mohtashim and contributors
// For license information, please see license.txt

frappe.query_reports["Summarized Stock Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
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
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
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
				method: "stock_addon.stock_addon.report.summarized_stock_report.summarized_stock_report.get_pdf_html",
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
