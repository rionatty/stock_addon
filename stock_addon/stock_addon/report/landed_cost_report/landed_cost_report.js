// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.query_reports["Landed Cost Report"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
			fieldname: "landed_cost_voucher",
			label: __("Landed Cost Voucher"),
			fieldtype: "Link",
			options: "Landed Cost Voucher",
			get_query: () => ({ filters: { docstatus: 1 } }),
		},
		{
			fieldname: "supplier",
			label: __("Charge Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		// Section headers, per-voucher totals and the grand total in bold
		if (data.bold) {
			value = `<b>${value}</b>`;
		}
		// Voucher header rows: brand the whole line
		if (data.is_header && column.fieldname === "particulars") {
			value = `<span style="color: var(--primary-color, #1d3557)">${value}</span>`;
		}
		// New landed price shown in green so it pops against FOB
		if (
			!data.is_total && !data.is_grand_total &&
			(column.fieldname === "landed_rate" || column.fieldname === "landed_amount") &&
			data.landed_amount
		) {
			value = `<span style="color: var(--green-600, #28a745)">${value}</span>`;
		}
		// Expense lines: orange amounts
		if (data.is_expense && column.fieldname === "lc_amount") {
			value = `<span style="color: var(--orange-600, #fd7e14)">${value}</span>`;
		}
		return value;
	},
};
